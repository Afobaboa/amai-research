"""Check the actual HTTP worker without needing public DNS or TLS."""
import os
import urllib.request


host = os.environ['ALLOWED_HOSTS'].split(',')[0].strip().lstrip('.')
if host == '*':
    host = 'localhost'
request = urllib.request.Request('http://127.0.0.1:8000/login/', headers={
    'Host': host,
    'X-Forwarded-Proto': 'https',
})
with urllib.request.urlopen(request, timeout=5) as response:
    if response.status != 200:
        raise SystemExit(f'Unexpected HTTP status: {response.status}')
