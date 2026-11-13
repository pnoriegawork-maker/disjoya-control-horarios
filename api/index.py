"""Punto de entrada ASGI para Vercel Serverless Functions.
Vercel busca automáticamente la variable `app` en este archivo."""
import sys
from pathlib import Path

# Agregar el directorio raíz al path de Python
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app

