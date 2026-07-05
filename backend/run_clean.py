#!/usr/bin/env python3
"""Wrapper to run ProcureAI backend with clean sys.path."""
import sys, os

# Remove Hermes-agent contamination from sys.path
sys.path = [
    p for p in sys.path 
    if 'hermes-agent' not in p and '.hermes' not in p
]
# Ensure venv site-packages is at the front
venv_sp = os.path.join(os.path.dirname(__file__), '.venv', 'lib', 'python3.12', 'site-packages')
if os.path.isdir(venv_sp) and venv_sp not in sys.path:
    sys.path.insert(0, venv_sp)

# Also clean PYTHONPATH environment
for key in ('PYTHONPATH', 'VIRTUAL_ENV'):
    os.environ.pop(key, None)

# Now run main
import main
if __name__ == '__main__':
    import uvicorn
    uvicorn.run(
        'main:app',
        host=os.getenv('HOST', '0.0.0.0'),
        port=int(os.getenv('PORT', '8000')),
        reload=False,
    )
