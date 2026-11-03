#!/usr/bin/env python3
"""
Puente IA <-> MCP para JobSwiper.

En lugar de llamar a una API de pago (Gemini), JobSwiper puede delegar cada
peticion de IA a un cliente MCP (Claude Code, Antigravity, ...). Como un
servidor MCP NO puede "llamar" al modelo (es el modelo quien llama al
servidor), el flujo se invierte:

    app.py --submit()--> [ cola de tareas ] <--tomar_tarea()-- mcp_server.py --> Claude
       ^                                                                          |
       +------------------ deliver() <-- responder_tarea() <----------------------+

submit() BLOQUEA el hilo que pide la IA hasta que Claude entrega el resultado
(o hasta el timeout), asi que gemini_json()/gemini_text() conservan su firma y
ningun otro punto del codigo necesita cambiar.

Sin dependencias externas: solo stdlib.
"""
import threading
import time
import uuid
from collections import OrderedDict

# Si un cliente reclama una tarea y no responde en este tiempo, vuelve a la cola
# (por ejemplo si cierras Claude a mitad de una generacion).
TAKEN_TTL = 240.0

# Cuanto tiempo (s) consideramos que "hay un cliente MCP conectado" desde su
# ultima llamada.
CLIENT_ALIVE = 90.0


class _Task:
    __slots__ = ('id', 'kind', 'label', 'prompt', 'history', 'created',
                 'status', 'taken_at', 'client', 'result', 'error')

    def __init__(self, kind, label, prompt, history):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind              # 'json' | 'text'
        self.label = label or 'Peticion de IA'
        self.prompt = prompt
        self.history = history or []
        self.created = time.time()
        self.status = 'pending'       # pending | taken | done | error | timeout
        self.taken_at = 0.0
        self.client = ''
        self.result = ''
        self.error = ''

    def public(self, with_prompt=True):
        d = {
            'id': self.id,
            'kind': self.kind,
            'label': self.label,
            'status': self.status,
            'age': round(time.time() - self.created, 1),
            'chars': len(self.prompt or ''),
            'client': self.client,
        }
        if with_prompt:
            d['prompt'] = self.prompt
            d['history'] = self.history
        else:
            d['preview'] = (self.prompt or '')[:180].replace('\n', ' ')
        return d


class Bridge:
    def __init__(self):
        self._cv = threading.Condition()
        self._tasks = OrderedDict()   # id -> _Task (solo las vivas)
        self._last_client = 0.0       # ultima vez que un cliente MCP nos hablo
        self._served = 0              # tareas resueltas por MCP en esta sesion

    # ----------------------------------------------------------- productor #
    def submit(self, prompt, *, kind='text', label='', history=None, timeout=900.0):
        """Encola una peticion de IA y BLOQUEA hasta tener respuesta.

        Devuelve el texto crudo que entrego el modelo.
        Lanza RuntimeError si hubo error o se agoto el tiempo.
        """
        task = _Task(kind, label, prompt, history)
        with self._cv:
            self._tasks[task.id] = task
            self._cv.notify_all()

        deadline = time.time() + float(timeout)
        with self._cv:
            while task.status in ('pending', 'taken'):
                remaining = deadline - time.time()
                if remaining <= 0:
                    task.status = 'timeout'
                    break
                self._cv.wait(min(remaining, 1.0))
                self._requeue_stale()
            self._tasks.pop(task.id, None)
            if task.status == 'done':
                self._served += 1

        if task.status == 'done':
            return task.result
        if task.status == 'error':
            raise RuntimeError(task.error or 'El cliente MCP devolvio un error.')
        raise RuntimeError(
            'Se agoto el tiempo esperando a Claude por MCP ({}s). '
            'Comprueba que el servidor MCP "jobswiper" este conectado y pidele '
            'que atienda las tareas pendientes.'.format(int(timeout)))

    # ----------------------------------------------------------- consumidor #
    def pull(self, *, wait=0.0, client=''):
        """Reclama la siguiente tarea pendiente. Espera hasta `wait` segundos."""
        deadline = time.time() + float(wait or 0)
        with self._cv:
            self._last_client = time.time()
            while True:
                self._requeue_stale()
                for task in self._tasks.values():
                    if task.status == 'pending':
                        task.status = 'taken'
                        task.taken_at = time.time()
                        task.client = client or 'mcp'
                        return task.public()
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
                self._cv.wait(min(remaining, 1.0))

    def deliver(self, task_id, content=None, error=None):
        """Entrega el resultado de una tarea. Devuelve True si se acepto."""
        with self._cv:
            self._last_client = time.time()
            task = self._tasks.get(task_id)
            if task is None:
                return False
            if task.status in ('done', 'error', 'timeout'):
                return False
            if error:
                task.status = 'error'
                task.error = str(error)
            else:
                task.status = 'done'
                task.result = content or ''
            self._cv.notify_all()
            return True

    def cancel(self, task_id, reason='Cancelada por el usuario.'):
        return self.deliver(task_id, error=reason)

    # ------------------------------------------------------------- consulta #
    def snapshot(self, *, touch=False):
        with self._cv:
            if touch:
                self._last_client = time.time()
            self._requeue_stale()
            tasks = [t.public(with_prompt=False) for t in self._tasks.values()]
            pending = sum(1 for t in tasks if t['status'] == 'pending')
            taken = sum(1 for t in tasks if t['status'] == 'taken')
            return {
                'pending': pending,
                'taken': taken,
                'waiting': pending + taken,
                'served': self._served,
                'client_connected': (time.time() - self._last_client) < CLIENT_ALIVE,
                'last_client_seen': round(time.time() - self._last_client, 1) if self._last_client else None,
                'tasks': tasks,
            }

    def client_connected(self):
        with self._cv:
            return (time.time() - self._last_client) < CLIENT_ALIVE

    # -------------------------------------------------------------- interno #
    def _requeue_stale(self):
        """Devuelve a la cola las tareas reclamadas que nadie respondio."""
        now = time.time()
        for task in self._tasks.values():
            if task.status == 'taken' and (now - task.taken_at) > TAKEN_TTL:
                task.status = 'pending'
                task.client = ''


BRIDGE = Bridge()
