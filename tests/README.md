# Pruebas

El gestor solo se ejecuta en Linux. En Windows, las pruebas corren dentro de un contenedor Linux con Docker Desktop.

## Unitarias

```bash
python3 -m unittest discover -s tests -v      # Linux
pwsh tests/run-unit.ps1                       # Windows (Docker Desktop)
```

Cubren la validación de la configuración, el Compose generado, el Caddyfile (HTTPS ACME, CA interna, túnel, puerta de mantenimiento, bloqueo de API internas), los patrones CSRF de Share, el catálogo de paquetes oficiales, el respaldo previo a actualizar y su vuelta atrás, la recuperación de operaciones interrumpidas y que los idiomas cubran todas las claves usadas.

Prueba de la interfaz curses en un pseudo-terminal: `python3 tests/pty_smoke.py [carpeta-de-estado] [teclas…]`.

## Integración (instalación real)

Requiere unos 10 GiB de RAM libres en Docker y descarga varios GB de imágenes:

```bash
docker build -t acdm-test-runner:local -f tests/Dockerfile .
docker volume create acdm-it-state
docker run --rm -v "$PWD:/workspace:ro" -v acdm-it-state:/acdm-state \
  -v /var/run/docker.sock:/var/run/docker.sock acdm-test-runner:local \
  python3 -m acdm --state-dir /acdm-state/s install --storage named \
  --bind 127.0.0.1 --http-port 18080 --https-port 18443 --confirm INSTALL
```

Después: `http://127.0.0.1:18080/share/` con el usuario `admin` y la contraseña que muestra la instalación.
