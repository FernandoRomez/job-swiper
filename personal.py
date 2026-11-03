"""Datos personales del candidato (contacto, educación, ciudad).

Se leen de `perfil.json` (ignorado por git). Si no existe, se usa
`perfil.example.json` para que la app arranque igual. Copia el ejemplo:

    cp perfil.example.json perfil.json
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PERFIL_PATH = os.path.join(HERE, 'perfil.json')
EXAMPLE_PATH = os.path.join(HERE, 'perfil.example.json')


def _load():
    for p in (PERFIL_PATH, EXAMPLE_PATH):
        if os.path.exists(p):
            try:
                with open(p, encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
    return {}


_P = _load()
NOMBRE = _P.get('nombre', 'Tu Nombre')
EMAIL = _P.get('email', '')
TEL_MX = _P.get('tel_mx', '')
TEL_US = _P.get('tel_us', '')
LINKEDIN = _P.get('linkedin', '')
WEB = _P.get('web', '')
CIUDAD = _P.get('ciudad', {'es': 'Ciudad, País', 'en': 'City, Country'})
ESCUELA_CORTA = _P.get('escuela_corta', '')
EDU = _P.get('edu', {})
SLUG = '_'.join(NOMBRE.split()[:3]) or 'Candidato'


def contacto():
    return {'nombre': NOMBRE, 'email': EMAIL, 'tel_mx': TEL_MX, 'tel_us': TEL_US,
            'linkedin': LINKEDIN, 'web': WEB}
