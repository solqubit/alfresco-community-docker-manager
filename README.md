**Español** | [English](README.en.md)

# Alfresco Community Docker Manager

Instala y administra **Alfresco Community** como **intranet** en tu servidor
Linux, desde un menú sencillo y sin escribir archivos de configuración. Usa las
imágenes oficiales de Alfresco con Docker, HTTPS local desde el primer momento e
interfaz en español e inglés.

Versión **0.1.0** · [Licencia MIT](LICENSE)

![Menú principal de Alfresco Community Docker Manager en español](docs/images/main-menu-es.png)

*Menú principal con una instalación en marcha.*

## Qué puedes hacer

- **Instalar Alfresco en minutos:** el gestor descarga la versión oficial más
  reciente y deja todo listo: Share, Content App, Control Center, búsqueda con
  Elasticsearch y base de datos PostgreSQL.
- **Entrar con seguridad desde el primer momento:** la contraseña de fábrica
  `admin/admin` se cambia sola por una segura antes de abrir el acceso, y cada
  instalación genera sus propias claves de cifrado.
- **HTTPS local desde la instalación:** certificados propios para la IP del
  servidor o nombres como `alfresco.lan`, que se renuevan solos.
- **Actualizar con un clic:** el gestor avisa cuando Alfresco publica una
  versión nueva y la aplica guardando antes la base de datos. Si algo falla,
  vuelve a la versión anterior.
- **Aprovechar la memoria del servidor:** Alfresco usa más memoria si el
  servidor tiene 16 GB o más, sin configurar nada.
- **Enviar correos (opcional):** invitaciones, notificaciones y tareas, con tu
  servidor de correo.
- **Revisar el estado:** servicios, direcciones, registros y comprobaciones de
  salud cuando las necesites.
- **Cambiar contraseñas** de cualquier usuario y **liberar espacio** de
  versiones antiguas.

## Antes de empezar

| | Mínimo | Recomendado |
| --- | --- | --- |
| Memoria RAM | **8 GB** | 16 GB |
| Disco | **15 GB libres** | Disco de **50 GB** (o más, según tus documentos) |
| Procesadores | 2 | 4 |
| Sistema | Linux de 64 bits (amd64 o arm64) con acceso **sudo** | |

También necesitas conexión a internet para descargar Alfresco. Si falta Docker
o Python, el asistente te ofrece instalarlos.

Antes de instalar, el gestor revisa tu servidor y te muestra el resultado:

![Revisión de requisitos antes de instalar](docs/images/requirements-es.png)

- **Memoria:** con menos de 8 GB te avisa de que es probable que Alfresco no
  pueda arrancar por falta de memoria. Puedes continuar bajo tu
  responsabilidad.
- **Disco:** la instalación ocupa unos 12 GB. Con menos de 15 GB libres, o si el
  disco quedaría por encima del 85 % (a partir de ahí la búsqueda deja de
  indexar), no deja instalar. Con menos de 35 GB libres avisa de que, para
  actualizar, tendrás que liberar espacio antes.

Por ejemplo, un disco de 30 GB con Ubuntu recién instalado deja unos 23 GB
libres: Alfresco cabe, pero cada actualización necesitará liberar espacio. Con
un disco de 50 GB actualizas sin preocuparte y te quedan unos 13–20 GB para
documentos.

## Empezar

**1. Descarga el gestor y ábrelo**

```bash
git clone https://github.com/solqubit/alfresco-community-docker-manager.git
cd alfresco-community-docker-manager
sudo bash start.sh
```

**2. Sigue el asistente**

1. Elige el idioma.
2. Elige **Instalar Alfresco**. El gestor revisa los requisitos del servidor.
3. Revisa los datos propuestos (IP del servidor, HTTPS local, puertos, zona
   horaria, carpeta de datos y, si quieres, el correo saliente) y confirma. La
   primera instalación tarda unos minutos.

**3. Entra en Alfresco**

Al terminar, el gestor te muestra las direcciones, la **contraseña de admin** y
el archivo de la **CA local**. Guarda la contraseña; también puedes verla
después en **Cuentas → Credenciales de acceso**.

| Aplicación | Dirección |
| --- | --- |
| Alfresco Share (interfaz principal) | `https://IP_DEL_SERVIDOR/share/` |
| Alfresco Content App | `https://IP_DEL_SERVIDOR/content-app/` |
| Control Center (administración) | `https://IP_DEL_SERVIDOR/control-center/` |

**4. Instala la CA en los equipos**

Para que el navegador no muestre el aviso de «conexión no segura», instala una
vez el certificado de la CA en cada equipo que use Alfresco. El gestor te indica
dónde está el archivo (`/opt/alfresco-community-docker-manager/local-ca.crt`), y
lo puedes volver a exportar en **Dominios y acceso → Exportar CA para HTTPS
local**.

- **Windows:** doble clic en el archivo → *Instalar certificado* → *Equipo
  local* → *Entidades de certificación raíz de confianza*. En una empresa se
  puede repartir con una directiva de grupo.
- **Firefox** usa su propio almacén: *Ajustes → Privacidad y seguridad → Ver
  certificados → Autoridades → Importar*.

## HTTPS local

Alfresco está pensado para la **red interna**: no se publica en internet. Para
entrar desde fuera, conéctate a la red con la VPN de tu empresa (por ejemplo,
WireGuard o Tailscale).

| Certificado | Validez | Renovación |
| --- | --- | --- |
| CA raíz (la que instalas en los equipos) | 10 años | Se instala una vez |
| CA intermedia | 7 días | Automática |
| Certificado de cada dirección | 12 horas | Automática, sin cortes |

Puedes entrar por la **IP** del servidor o añadir **nombres locales** como
`alfresco.lan` en **Dominios y acceso → Direcciones locales → Añadir otra
dirección local**:

1. Escribe la IP o el nombre. **Usar HTTPS local** viene activado; el
   certificado se crea solo.
2. Si es un **nombre**, haz que apunte al servidor en el router o DNS de la red
   (por ejemplo `alfresco.lan → 192.168.1.10`) o en el archivo `hosts` de cada
   equipo. Evita los nombres que terminan en `.local`; mejor `.lan` o
   `.internal`.

Puedes tener varias direcciones a la vez; todas usan la misma CA. Cada
certificado vale solo para su dirección exacta. La **dirección principal** es la
que usa Alfresco para generar enlaces.

Quién puede llegar al servidor lo decides en tu red (cortafuegos, router, VPN):
el gestor no lo restringe.

## Correo saliente (opcional)

Sin correo, Alfresco funciona igual, pero no envía invitaciones a sitios,
notificaciones ni avisos de tareas. Puedes configurarlo al instalar o después en
**Configuración → Correo saliente**. Necesitas los datos de una cuenta de envío
(SMTP) de tu correo o de un servicio de envío:

| Dato | Ejemplo |
| --- | --- |
| Servidor SMTP | `smtp.office365.com`, `smtp.gmail.com`, `mail.tuempresa.com` |
| Puerto y seguridad | `587` con STARTTLS (lo habitual), `465` con SSL |
| Usuario y contraseña | La cuenta que envía (Gmail necesita una contraseña de aplicación) |
| Remitente | `alfresco@tuempresa.com` |

Antes de guardar, el gestor envía un **correo de prueba** para confirmar que los
datos funcionan. La contraseña se guarda de forma privada y no aparece en los
registros. Alfresco se reinicia para aplicar el cambio (1–3 minutos).

## Memoria

El gestor asigna la memoria de cada servicio según la RAM del servidor:

| RAM del servidor | Alfresco | Elasticsearch | Transformaciones | Share |
| --- | --- | --- | --- | --- |
| 8 GB | 2 GB | 2 GB | 1,5 GB | 1 GB |
| 16 GB | 4 GB | 3 GB | 2 GB | 1,5 GB |
| 32 GB o más | 8 GB | 4 GB | 3 GB | 2 GB |

Si amplías la RAM de tu servidor, el ajuste se aplica la próxima vez que inicies
Alfresco, actualices o cambies la configuración desde el gestor.

## Volver a abrir el gestor

Desde cualquier carpeta:

```bash
sudo bash /opt/alfresco-community-docker-manager/start.sh
```

Muévete con **↑ / ↓**, cambia de columna con **← / →**, entra con **Enter** y
vuelve con **Esc**.

## Actualizaciones

En **Actualizaciones → Ver y aplicar actualizaciones** verás si hay una versión
nueva de Alfresco o de sus componentes. Al aplicarla, el gestor:

1. Detiene Alfresco un momento y guarda la base de datos.
2. Actualiza a la vez todos los componentes que cambian.
3. Arranca y comprueba que todo responde.

Antes de empezar, el gestor calcula el espacio que necesitan las imágenes
nuevas (desde ~1 GB si solo cambia Share hasta ~13 GB si cambia casi todo). Si
no hay sitio, no empieza y te pide usar **Salud → Liberar espacio**. Las
versiones anteriores ocupan disco hasta que lo usas.

Tus documentos no se tocan. Si la actualización se interrumpe o falla, entra en
**Recuperar operación pendiente** para continuarla o volver a la versión
anterior.

Para actualizar el propio gestor, descarga la versión nueva y elige
**Gestor → Actualizar el gestor**.

## Dónde se guardan los datos

Todo queda en **`/opt/alfresco-community-docker-manager/`**, fuera de la carpeta
descargada: configuración, documentos, base de datos, certificados y registros.
Puedes borrar la carpeta descargada después de instalar.

> El gestor no hace copias de seguridad completas. Para protegerte de la pérdida
> del servidor, usa instantáneas de la máquina virtual o una copia externa del
> disco.

## Ayuda

- **Algo no arranca:** abre **Servicio → Registros** y elige el servicio. Si
  los servicios se cierran solos, suele faltar memoria (mínimo 8 GB).
- **El navegador dice «conexión no segura»:** falta instalar la CA en ese
  equipo (paso 4), o entras por una dirección sin HTTPS local activado.
- **Disco casi lleno:** **Salud → Comprobaciones de salud** avisa a partir del
  80 %. Usa **Salud → Liberar espacio** para borrar versiones antiguas.
- **No llegan los correos:** en **Configuración → Correo saliente → Enviar correo
  de prueba** verás el motivo (usuario o contraseña, puerto bloqueado…). Usa el
  puerto 587 si el 25 está bloqueado en tu red.
- **La búsqueda no encuentra algo:** en Share, cambia el alcance a
  **Repositorio** y usa `*` para buscar parte de una palabra (`infor*`). Los
  documentos nuevos tardan unos segundos en aparecer.
- **Comandos sin menú:** `sudo bash start.sh --help` muestra todas las acciones
  (estado, actualizar, direcciones, registros…).

[Pruebas realizadas](docs/VALIDACION.es.md) · Proyecto independiente basado en
GLPI Docker Manager y Nextcloud Docker Manager. Alfresco, PostgreSQL,
Elasticsearch y Caddy conservan sus propias licencias.
