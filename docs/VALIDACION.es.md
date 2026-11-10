# Pruebas realizadas

> **Cambios posteriores a esta validación (10 de noviembre de 2026):**
> - Se retiró el soporte de Solr. El gestor solo instala y actualiza versiones con Elasticsearch (Alfresco 26.2 o posterior). Las filas sobre la instalación 26.1 con Solr y su paso a Elasticsearch quedan como registro histórico.
> - Se retiraron los respaldos manuales (`backup`, `verify-backup`, `restore` y el menú **Respaldos**). Solo queda el respaldo automático de base de datos y certificados antes de cada actualización. **Recuperar → Volver a la versión anterior** lo restaura sin tocar los documentos. Las filas sobre respaldos completos y restauraciones manuales quedan como registro histórico.
> - Se retiró la comprobación diaria programada (temporizador de systemd); **Comprobaciones de salud** queda como acción manual.
> - Correo saliente opcional, probado con un servidor SMTP de pruebas (Mailpit) que exige autenticación y una contraseña con comillas, `$` y comillas invertidas: se rechazó la contraseña incorrecta, llegó el correo de prueba del gestor y Alfresco envió su propia notificación de tarea con el remitente configurado.
> - Memoria según la RAM: la instalación de prueba (30 GiB) pasó al perfil grande (Alfresco 8 GiB, Elasticsearch 4 GiB) y siguió sana.
> - Almacén de claves propio: una instalación nueva lo generó con `keytool`, Alfresco arrancó sin errores de claves, volvió a arrancar tras reiniciar el contenedor y los registros no contienen contraseñas. La instalación temporal se eliminó después.
> - **Solo intranet:** se retiraron la opción VPS, los dominios públicos con Let's Encrypt y Cloudflare Tunnel. Las filas sobre esos modos quedan como registro histórico. HTTPS local viene activado desde la instalación y el proxy solo atiende a redes privadas (LAN y VPN). Probado en la instalación de prueba: `https://127.0.0.1` por IP, sin nombre de servidor en la conexión, respondió 200 con la CA; el certificado es para esa IP exacta; HTTP redirige a HTTPS; el inicio de sesión en Share por HTTPS funcionó y se rechazó un origen ajeno. Vigencias leídas de los certificados reales: CA raíz 10 años, intermedia 7 días, certificado de cada dirección 12 horas (renovación automática).
> - Después de los primeros cambios: 38 pruebas unitarias correctas; la instalación de prueba 26.2.0 se reconfiguró con la versión nueva y siguió respondiendo (Share, búsqueda por nombre y contenido); el panel curses ya no muestra **Respaldos**.

10 de noviembre de 2026. Windows 11 con Docker Desktop, motor Linux amd64, aproximadamente 30 GiB disponibles para Docker y Python 3.12 en `acdm-test-runner:local`. El gestor se ejecutó dentro de contenedores con el socket Docker del host. Se siguieron los patrones de `nextcloud-docker-manager/tests/run-integration.ps1` y su informe de validación.

## Aislamiento

| Instalación | Estado | Puertos | Almacenamiento |
|---|---|---|---|
| Protegida, existente | `acdm-it-state`, id `ff1cd98949c7d89a` | `18080/18443` | Conservada |
| Prueba principal | `acdm-codex-state`, ruta `/acdm-state/s`, id `14d719f2ef9e851e` | `28080/28443`, cambio temporal a `28081` | Volúmenes Docker |
| Prueba de carpetas | Mismo volumen de estado, ruta `/acdm-state/d`, id `3acd4e4fbf32d272` | `38080/38443` | Bind dentro de la VM Linux |

La carpeta bind se obtuvo mediante `docker volume inspect --format '{{.Mountpoint}}' acdm-codex-bind` y se montó dentro del ejecutor en esa misma ruta absoluta. Esto permitió que Python y el daemon vieran los mismos directorios Linux.

Los contenedores auxiliares usaron la etiqueta `com.solqubit.acdm.test=codex-20261110`. Las operaciones sobre instalaciones se limitaron a los dos identificadores de prueba. Se conservaron los cambios que ya existían en el árbol de trabajo.

## Resultados

| Área | Resultado | Evidencia |
|---|---|---|
| Pruebas unitarias iniciales | OK | 23 casos |
| Pruebas unitarias con correcciones | OK | 37 casos |
| Instalación anterior | OK | Alfresco `26.1.0`, bundle `v10.6.0`, Solr `2.0.20`, PostgreSQL `17.9` |
| Detección de actualización | OK | `check-updates` anunció `26.2.0`, bundle `v10.9.0`, cambio a Elasticsearch |
| Actualización Solr a Elasticsearch | OK | Instalación con volúmenes y con carpetas; volumen de búsqueda nuevo y batch-indexing `5.9.0` |
| Esquema y módulos | OK | El descriptor informó esquema `21,100`, original `21,000`; actualización de Share Services `26.1.0.61` a `26.2.0.96` |
| Acceso después de actualizar | OK | Basic auth contra `people/-me-` devolvió 200; `/share/page/` devolvió 200 |
| Reindexación tras actualizar | OK | Documento creado antes de actualizar encontrado mediante la API Search; ciclo de 838 nodos, cero nodos fallidos |
| Respaldo completo | OK | SQL, certificados y content store; aproximadamente 17.2 MiB |
| Respaldo sin documentos | OK | SQL y certificados; aproximadamente 190 KiB |
| Verificación de respaldos | OK | Ambos archivos aceptados por `verify-backup` |
| Restauración completa | OK | Repetida sin los volúmenes anteriores; `content.tar` extraído a un volumen nuevo, contenido anterior recuperado y documento posterior ausente |
| Restauración sin documentos | OK | Conservó el volumen de contenido activo; contenido anterior legible y documento posterior ausente |
| Búsqueda de documentos antiguos al restaurar | OK | Restauración completa y sin documentos repetidas; Search encontró el documento que quedaba fuera de la ventana reciente |
| Corte durante actualización | OK | Ejecutor terminado con SIGKILL en fase `applying`; respaldo publicado y `recover --action resume` completado |
| Corte durante respaldo | OK | Ejecutor terminado en `backup-complete`, durante la reapertura; `recover --action restore` creó volúmenes nuevos y reabrió el servicio |
| HTTPS local | OK | `alfresco.lan:28443`, CA exportada y validación TLS sin `--insecure` |
| CSRF de Share | OK | POST de login con origen configurado devolvió 302; origen ajeno rechazado por CSRFFilter |
| Dominio principal y eliminación | OK | Cambio a `alfresco.lan`, vuelta a `127.0.0.1` y eliminación del dominio adicional |
| Cambio de red | OK | `network --http-port 28081`; API autenticada accesible en el puerto nuevo |
| Rutas internas | OK | `/alfresco/s/api/solr/aclchangesets` y `/alfresco/service/prometheus` devolvieron 403 |
| Contraseñas y credenciales | OK | Admin y usuario REST; contraseña anterior 401, nueva 200; credenciales guardadas coincidentes |
| Mantenimiento y limpieza | OK | Cero errores y avisos, respaldo válido; 21 volúmenes antiguos y 37 revisiones retirados, imágenes en uso conservadas |
| Actualización del gestor | OK | Copia temporal `0.1.1` ejecutable desde otra carpeta; copia anterior `0.1.0` conservada; fuente con sintaxis inválida rechazada |
| Interfaz curses | OK | Español e inglés, 80×24 y 50×18, navegación y retorno con Esc |
| Modo plain | OK | Menú público por CLI, entrada a dominios y respaldos, retorno mediante `b` |
| Propiedad de carpetas | OK | Solr, Elasticsearch y Alfresco arrancaron y escribieron en almacenamiento bind |
| Eliminación de auxiliares de Solr | OK | Nueva restauración a 26.1.0 y actualización a 26.2.0; ambos volúmenes auxiliares capturados antes y ausentes después |
| Limpieza final y recursos protegidos | PARCIAL | Instalaciones, redes y volúmenes etiquetados retirados; quedan 11 anónimos anteriores a las correcciones pendientes de autorización por la restricción de etiquetas |

### Documentos y respaldos

Se creó `codex-before.txt` mediante `POST /nodes/-shared-/children` y se cargó contenido mediante `PUT /nodes/{id}/content`. Su identificador en la instalación principal fue `943c3c8f-4c18-4853-bc3c-8f4c1888534a`. El contenido JSON contenía el marcador `before-backup`.

Después de respaldar se crearon documentos adicionales. Tras restaurar, la descarga del documento anterior devolvió el contenido esperado y la consulta del documento posterior devolvió 404. La variante sin documentos conservó expresamente el nombre del volumen de contenido que estaba activo al iniciar la restauración.

Los primeros respaldos manuales fueron `20261110T162352Z-44571ebf.acdm.tar`, completo, y `20261110T162610Z-f7827a0f.acdm.tar`, sin documentos. El respaldo interrumpido y recuperado fue `20261110T163344Z-8f6deb91.acdm.tar`.

La búsqueda adicional encontró un fallo que la descarga directa no detectaba. Con un índice recién creado, el indexador arrancaba desde la ventana reciente y omitía el documento anterior. La API Search devolvía 200 con cero coincidencias, mientras la API de contenido devolvía 200 con los datos correctos. Se repitieron las restauraciones después de corregir la configuración del cursor.

La comprobación posterior sin los volúmenes anteriores detectó un segundo fallo de restauración. El archivo de documentos no se estaba extrayendo, pese a estar incluido en el respaldo. Las primeras pruebas con el volumen original presente no detectaban esta condición. La prueba más estricta produjo `CONTENT INTEGRITY ERROR` y el repositorio no llegó a estar listo. Se interrumpió ese ejecutor y se repitió la recuperación con la corrección.

La recuperación corregida usó el respaldo completo `20261110T165301Z-8729602d.acdm.tar`. Creó `acdm_14d719f2ef9e851e_content_rde8cc681`, recuperó el marcador `before-backup` con HTTP 200 y permitió encontrar el documento por Search. El acceso HTTPS con la CA exportada y la autenticación del usuario REST también devolvieron 200 después de recuperar.

### Interfaz

Se hicieron 96 recorridos de widgets reales en pseudo-terminal mediante `Controller.action` y sus pantallas asociadas. Incluyeron dashboard, estado, confirmación de parada cancelada, registros, actualizaciones, respaldos, dominios, idioma, selector de usuarios, credenciales, mantenimiento, vista previa de limpieza, actualización del gestor y recuperación. Los recorridos adicionales abrieron formularios de puertos y dominios locales/directos/túnel, selectores de eliminación/principal/TLS, detalles de dirección y detalle de respaldo. Los cuatro últimos recorrieron el informe de mantenimiento real, con cuatro certificados, después de corregir sus traducciones.

Se capturó el contenido real de la pantalla curses antes de leer cada tecla. A 50×18 se verificaron las leyendas, el desplazamiento del dashboard y el retorno con Esc. Las líneas largas de contexto y resumen se recortan al ancho disponible. Se probó el selector inicial mediante `bash /workspace/start.sh` y el modo plain mediante `python3 -m acdm --plain menu`. En plain se usó el retorno documentado `b`.

Estos recorridos abren y cancelan formularios. Las instalaciones, actualizaciones, restauraciones y cambios efectivos se verificaron mediante CLI.

### Propiedad del almacenamiento bind

Propietarios y permisos observados después de arrancar las imágenes.

| Datos | UID y GID | Modo observado |
|---|---|---|
| Alfresco `content` | `0:1000` | `775` |
| Solr `search` | `33007:33007` | `755` |
| Elasticsearch `search` nuevo | `1000:0` | `775` |

Elasticsearch y Alfresco permitieron crear, recuperar y buscar un documento por las API públicas en la instalación bind.

## Fallos encontrados y corregidos

### Colisión de la dirección del proxy

Al reanudar la actualización, Compose falló con `Address already in use`. La red mostró a Control Center usando `172.23.128.2`, que también estaba fijada para el proxy. La dirección estática no estaba reservada frente a la asignación dinámica durante la recreación de los servicios.

El proxy usa ahora asignación dinámica. `Engine.up()` vuelve a generar la definición Compose antes de arrancar para aplicar la corrección también a revisiones guardadas por versiones anteriores. La misma recuperación se repitió correctamente después del cambio. Se añadieron pruebas de ambas condiciones.

### Reanudación antes de activar el destino

El registro podía llegar a `applying` antes de detener los contenedores y activar la revisión nueva. `recover --action resume` arrancaba la revisión que siguiera activa y podía dar por completada la actualización con el origen todavía seleccionado.

La recuperación comprueba y activa el destino registrado antes de continuar. La ventana anterior a la activación está cubierta por una prueba unitaria que falló con el código inicial. El corte real capturado en esta sesión ocurrió con el destino ya activado.

### Importación SQL incompleta

Una migración a un volumen PostgreSQL nuevo no registraba que la importación hubiera concluido. Reanudar podía arrancar un volumen parcialmente importado.

Se registra `database_ready` después de importar. Si el volumen SQL cambia y esa marca falta, la reanudación conserva la operación pendiente e indica `recover --action restore`. Las pruebas cubren importación completa e incompleta. La migración real entre versiones mayores de PostgreSQL queda fuera de esta sesión.

### Etiquetas de mantenimiento sin traducción

El renderizado de los resultados mostraba `names`, `seconds_remaining`, `days_remaining`, `image_changed`, `proxy` y `postgres` como etiquetas. Se añadieron traducciones en español e inglés y una prueba del informe estructurado de certificados y actualizaciones.

### Volúmenes anónimos al retirar Solr

Además del volumen `search`, la imagen de Solr declara volúmenes para `solrhome` y `keystores`. `compose up --remove-orphans` retiraba el contenedor, pero dejaba esos dos volúmenes sin uso y sin la etiqueta del gestor.

El arranque comprueba la propiedad de todos los contenedores del proyecto y retira los servicios que desaparecen con sus volúmenes anónimos. Los volúmenes nombrados se conservan para la limpieza habitual. La eliminación completa de una instalación también incluye sus volúmenes anónimos. Las pruebas comprueban que un contenedor ajeno bloquea la operación.

También se cubrió mediante pruebas unitarias el caso en que Compose hereda un volumen anónimo como montaje explícito al recrear un servicio. `down --volumes` puede conservar ese montaje. El gestor retiene los nombres procedentes de los contenedores cuya propiedad ya verificó y comprueba la etiqueta de anonimato de Docker antes de completar su eliminación. La limpieza queda limitada a esos montajes asociados.

### Archivo de documentos omitido al restaurar

El manifiesto enumera `content.tar`, pero la condición de restauración buscaba `content`. Como resultado, incluso un respaldo completo seguía la rama que conserva el content store activo. Al faltar ese volumen, la base restaurada apuntaba a documentos inexistentes y Alfresco rechazaba el arranque.

La condición usa ahora el nombre real del archivo. Se añadieron pruebas que exigen un volumen de documentos nuevo y la llamada a `restore_volume` con `content.tar` para el respaldo completo. La variante sin documentos debe conservar el volumen activo y restaurar únicamente los certificados además del SQL.

### Reindexación histórica incompleta

Se activó `ALFRESCO_REINDEX_CONTINUOUS_SEEDFROMEARLIEST=true` para inicializar un cursor ausente desde el primer commit de la base. Se fijó `ALFRESCO_REINDEX_CONTINUOUS_MAXGAPAGE=0` para permitir el recorrido histórico completo. Se comprobó que ambos parámetros existen en la configuración de la imagen `5.9.0` utilizada.

El comportamiento de inicialización y la combinación de ambos parámetros se contrastaron con la implementación oficial de esa versión. [WatermarkBootstrap 5.9.0](https://github.com/Alfresco/alfresco-community-search/blob/5.9.0/alfresco-elasticsearch-batch-indexing/src/main/java/org/alfresco/elasticsearch/batchindexing/WatermarkBootstrap.java), [configuración 5.9.0](https://github.com/Alfresco/alfresco-community-search/blob/5.9.0/alfresco-elasticsearch-batch-indexing/src/main/resources/application.properties)

## Comandos principales

Construcción y pruebas pequeñas desde PowerShell.

```powershell
docker build -t acdm-test-runner:local -f tests/Dockerfile .
docker run --rm -v "D:/Github/alfresco-community-docker-manager:/workspace:ro" `
  acdm-test-runner:local python3 -m unittest discover -s tests -v
```

En la sesión se mantuvo un ejecutor con los montajes del repositorio, `acdm-codex-state:/acdm-state` y `/var/run/docker.sock:/var/run/docker.sock`. Los comandos se lanzaron con `docker exec`. El siguiente bloque representa la secuencia principal dentro de ese ejecutor.

```bash
acdm() { python3 -m acdm --state-dir /acdm-state/s "$@"; }

acdm install --alfresco-version 26.1.0 --storage named \
  --http-port 28080 --https-port 28443 --confirm INSTALL
acdm check-updates
acdm update --confirm UPDATE-26.2.0
acdm backup --confirm BACKUP
acdm backup --without-content --confirm BACKUP
acdm verify-backup /acdm-state/s/backups/20261110T162352Z-44571ebf.acdm.tar
acdm verify-backup /acdm-state/s/backups/20261110T162610Z-f7827a0f.acdm.tar
acdm restore /acdm-state/s/backups/20261110T162352Z-44571ebf.acdm.tar --confirm RESTORE
acdm restore /acdm-state/s/backups/20261110T162610Z-f7827a0f.acdm.tar --confirm RESTORE

acdm recover --action resume --confirm RESUME
acdm recover --action restore --confirm RESTORE

acdm domain-add alfresco.lan --local-https --confirm APPLY
acdm export-ca
acdm domain-primary alfresco.lan --confirm APPLY
acdm domain-primary 127.0.0.1 --confirm APPLY
acdm domain-remove alfresco.lan --confirm APPLY
acdm network --http-port 28081 --confirm APPLY

printf '%s\n' "$NEW_ADMIN_PASSWORD" | acdm reset-password admin --password-stdin --confirm RESET-PASSWORD
printf '%s\n' "$NEW_USER_PASSWORD" | acdm reset-password codex-test --password-stdin --confirm RESET-PASSWORD
acdm credentials
acdm maintenance
acdm cleanup --confirm CLEANUP
acdm self-update --source /tmp/acdm-self-update-source --confirm UPDATE-MANAGER
```

Los comandos de recuperación se ejecutaron después de terminar sus respectivos ejecutores mediante `docker kill`. Se esperó el campo `phase` de `operation.json`, sin modificar el registro de la operación real.

Las llamadas HTTP usaron `curl --config -` con autenticación transmitida por stdin. Desde los ejecutores se conectó al puerto publicado mediante `host.docker.internal`, conservando el nombre HTTP o TLS configurado con `--connect-to`. Para HTTPS se usó `--cacert /acdm-state/s/local-ca.crt` y para el login se enviaron cookies, los campos de formulario y el encabezado Origin con el puerto `28443`.

La consulta de búsqueda usó `POST /alfresco/api/-default-/public/search/versions/1/search` con el cuerpo siguiente.

```json
{"query":{"query":"cm:name:\"codex-before.txt\"","language":"afts"}}
```

La instalación bind usó los mismos comandos con `--state-dir /acdm-state/d`, `--storage directory`, `--storage-root /var/lib/docker/volumes/acdm-codex-bind/_data` y puertos `38080/38443`.

## Observaciones de las pruebas

- La protección CSRF de Share rechazó el origen ajeno con HTTP 500 y registró `Possible CSRF attack`. El origen permitido devolvió 302. Los 403 de las rutas internas fueron respuestas del proxy.
- Una petición de búsqueda coincidió con la recreación del proxy durante las pruebas de dominios y falló al conectar. Se repitió en estado estable. Esa repetición permitió distinguir el fallo de indexación histórica descrito arriba.
- Un respaldo lanzado antes de que acabara la recuperación fue rechazado con `Another operation is running`. Se repitió después de liberar el bloqueo.
- Los apagados de los servicios Java tardaron aproximadamente 90 segundos en varias operaciones. Se respetaron los tiempos del gestor.

## Limpieza

Se retiraron las dos instalaciones mediante `Engine.remove_owned(delete_volumes=True)` y se eliminaron sus generaciones antiguas comprobando `com.solqubit.acdm.installation`. La última pasada retiró 11 volúmenes etiquetados de la instalación bind y 10 de la principal. El inventario por identificador quedó sin contenedores, redes ni volúmenes etiquetados de prueba. La caché de imágenes compartida se conserva.

Se eliminaron los ejecutores, `acdm-codex-state`, `acdm-codex-bind` y los scripts temporales. Los 12 contenedores anteriores conservaron sus identificadores y los 14 volúmenes del inventario inicial siguieron presentes. La instalación protegida conservó sus diez contenedores en ejecución. La pasada final de 37 pruebas se hizo en un ejecutor nuevo, con únicamente el repositorio montado en modo lectura.

Los volúmenes anónimos que ya habían quedado separados de sus contenedores antes de aplicar las correcciones requieren una excepción a la condición del encargo que limita `docker volume rm` a recursos con etiqueta de instalación. Se solicitó autorización para los 11 nombres siguientes. Se excluyeron los seis volúmenes anónimos del inventario inicial del host.

```text
1f719119c3e67a14edeefc1d92303f910f792b4c83311c566d0838533492e767
9bb2bf1195c9492ca8254908c5025397bb1695d123ffe46cfd4d58eaa9b661b0
038c2a4b09acb4e269a65ad6cc56eee7bc6e18c3f4400eb3ac6a8a9fde3d3f24
48cf19bf47996c5c8a2e6df180d730208286073cc9b42d423b11a7b696cc5f2c
76f2c8bf0a78547da2b021b6fb22654ed65e2d5d97c1737d70b81dc7781e6073
76f7a31f449d7821d762506538dfd90238bb4442bad6e9bc486065ad6349e2f2
094f72b5f66257914cb9ca426df23d18d68aa030bb4ef554a8638875de8500e3
442f1704fbe1273ab7453cb366a210c19d6301003fd52a7e2a8fb74b1635a6fa
5117dbc0544bbccf4fc944c01676d03072712407f8342a3dc84865b2e645ddd6
f58c879e9769839b58670fa7247dabb32dfc5f6ec7a4ebda9877a199926b0df7
f65bb2d7217d97fde4d81a48be6b865da88d25c78417c7f7c673041644040d65
```

## Pendiente fuera de este entorno

Instalación de Python y Docker en una distribución Linux limpia, arranque y temporizadores systemd, reinicio del host, disco físico y pérdida real de un montaje, DNS público, firewall, certificados ACME reales y renovación, túneles externos, carga sostenida y arquitectura arm64.

Las dos versiones ensayadas utilizan PostgreSQL `17.9`. El cambio de versión mayor SQL y su interrupción solo están cubiertos por pruebas de lógica de recuperación. La inicialización histórica se comprobó con batch-indexing `5.9.0`; las imágenes anteriores de ese componente requieren una validación específica.

No se hicieron commits ni publicaciones remotas.
