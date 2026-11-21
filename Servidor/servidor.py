"""ClinAgenda - servidor web (solo biblioteca estándar de Python).

Uso:  python servidor.py   y abrir  http://localhost:8000

Variables de entorno (todas opcionales):
  PORT            puerto (por defecto 8000)
  CLINAGENDA_HOST dirección donde escucha (por defecto 127.0.0.1 = solo este equipo)
  MSLS_DB         ruta de la base de datos (por defecto MSLS.db en la carpeta principal)
  CLINAGENDA_PIN_DEMO  PIN de los usuarios de demostración (por defecto 1234)

Comandos de administración (piden el PIN por teclado, sin mostrarlo):
  python servidor.py usuario-nuevo "Nombre Apellido" recepcion|doctora
  python servidor.py cambiar-pin "Nombre Apellido"
"""
# getpass: pide el PIN en la terminal sin mostrarlo.
import getpass
# json: convierte entre texto JSON y diccionarios de Python.
import json
# os: lee variables de entorno (PORT, MSLS_DB...).
import os
# re: expresiones regulares (para reconocer rutas como /api/citas/5/cancelar).
import re
# secrets: genera números aleatorios seguros para los tokens de sesión.
import secrets
# sqlite3: base de datos; aquí solo se usa para los respaldos.
import sqlite3
# sys: argumentos de la línea de comandos y salida de errores.
import sys
# threading: candado para las sesiones y el hilo de respaldos.
import threading
# time: hora actual para vencer sesiones y esperar entre respaldos.
import time
# traceback: imprime el detalle técnico de un error en la consola.
import traceback
# datetime: nombre de los archivos de respaldo.
from datetime import datetime
# SimpleCookie: lee la cookie que envía el navegador.
from http.cookies import SimpleCookie
# Servidor web que viene con Python (un hilo por petición).
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
# Path: rutas de archivos que funcionan igual en Windows y Linux.
from pathlib import Path

# Estructura: <raiz>/Servidor/servidor.py, <raiz>/Codigos_Python/nucleo.py, <raiz>/estructura_html/, <raiz>/MSLS.db
# Carpeta donde está este archivo (Servidor/).
RAIZ_SERVIDOR = Path(__file__).resolve().parent
# Carpeta principal del proyecto (un nivel arriba).
RAIZ = RAIZ_SERVIDOR.parent
# Agrega Codigos_Python y Servidor a las rutas de importación...
for _ruta in (RAIZ / "Codigos_Python", RAIZ_SERVIDOR):
    # ...solo si todavía no están...
    if str(_ruta) not in sys.path:
        # ...para que 'import nucleo' funcione desde cualquier carpeta.
        sys.path.insert(0, str(_ruta))

# Reglas de negocio (validaciones, citas, usuarios).
import nucleo  # noqa: E402  (necesita el sys.path de arriba)
# Error pensado para mostrarse a la persona.
from nucleo import ErrorNegocio  # noqa: E402

# Carpeta con index.html y los dibujos.
CARPETA_WEB = RAIZ / "estructura_html"
# Ruta de la base de datos; se puede cambiar con la variable MSLS_DB.
RUTA_DB = os.environ.get("MSLS_DB", str(RAIZ / "MSLS.db"))
# Carpeta de copias de seguridad.
CARPETA_RESPALDOS = RAIZ / "respaldos"
# Tamaño máximo aceptado en una petición (100 KB): frena envíos abusivos.
MAX_CUERPO = 100_000

# Solo se sirven estos archivos (nada de rutas arbitrarias del disco).
# Lista cerrada de archivos que se pueden descargar. Cualquier otra ruta da 404 (nadie puede pedir MSLS.db ni el código).
ARCHIVOS_WEB = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/fondo_suave.svg": ("fondo_suave.svg", "image/svg+xml"),
    "/ilustracion_clinica.svg": ("ilustracion_clinica.svg", "image/svg+xml"),
}


# Crea las tablas y, si la base está vacía, datos inventados de demostración.
def preparar_base():
    """Crea las tablas y, si la base está vacía, datos INVENTADOS de demostración."""
    # Abre la base (crea las tablas si faltan).
    con = nucleo.conectar(RUTA_DB)
    # Solo si no hay clínicas (base nueva)...
    if not con.execute("SELECT 1 FROM clinicas").fetchone():
        # ...crea las tres clínicas...
        con.execute("INSERT INTO clinicas (nombre) VALUES ('Clínica Centro'),('Clínica Norte'),('Clínica Sur')")
        # ...cuatro médicos...
        con.execute("INSERT INTO medicos (nombre) VALUES ('Dr. Salazar'),('Dra. Mendoza'),('Dr. Vega'),('Dra. Castillo')")
        # ...y cuatro pacientes de ejemplo.
        for nombre, nac in [("Juan Pérez", "1980-05-10"), ("María López", "1975-11-02"),
                            ("Pedro Andrade", "2016-08-21"), ("Carmen Ruiz", "1948-03-14")]:
            # Usa la misma validación que la pantalla.
            nucleo.registrar_paciente(con, "sistema", nombre, nac)
    # Carga horarios por defecto a las clínicas que no tengan.
    sembrar_horarios(con)
    # Crea los usuarios de demostración si no hay ninguno.
    sembrar_usuarios(con)
    # Cierra la conexión.
    con.close()


# Horarios inventados de demostración; la clienta debe confirmarlos.
def sembrar_horarios(con):
    """Horarios INVENTADOS de demostración para cada clínica que aún no tenga uno.
    La clienta debe confirmarlos (ver DECISIONES.md)."""
    # Recorre cada clínica.
    for c in con.execute("SELECT id, nombre FROM clinicas").fetchall():
        # Si ya tiene horario...
        if con.execute("SELECT 1 FROM horarios WHERE clinica_id=?", (c["id"],)).fetchone():
            # ...no se toca.
            continue
        # Clínica Sur cierra más temprano.
        sur = "sur" in c["nombre"].lower()
        # Lunes a viernes: abre 08:00; cierra 13:00 en Sur y 17:00 en las demás.
        for dia in range(5):   # lunes a viernes
            # Guarda el horario del día.
            con.execute("INSERT INTO horarios VALUES (?,?,?,?)", (c["id"], dia, "08:00", "13:00" if sur else "17:00"))
        # Solo Centro y Norte atienden los sábados...
        if not sur:
            # ...de 08:00 a 12:00 (5 = sábado).
            con.execute("INSERT INTO horarios VALUES (?,?,?,?)", (c["id"], 5, "08:00", "12:00"))   # sábado


# Crea los usuarios de DEMOSTRACIÓN. Cambiar los PIN antes de usar datos reales.
def sembrar_usuarios(con):
    """Solo si no hay ningún usuario: crea los de DEMOSTRACIÓN. Cambiar los PIN antes de usarlo de verdad."""
    # Si ya hay usuarios, no hace nada...
    if con.execute("SELECT 1 FROM usuarios").fetchone():
        # ...para no pisar los existentes.
        return
    # PIN de demostración: 1234, o el de la variable CLINAGENDA_PIN_DEMO.
    pin = os.environ.get("CLINAGENDA_PIN_DEMO", "1234")
    # Dos de recepción y la doctora.
    for nombre, rol in [("Rosa (recepción)", "recepcion"), ("Recepción Sur", "recepcion"),
                        ("Dra. Paredes", "doctora")]:
        # Guarda cada usuario con la huella del PIN.
        nucleo.crear_usuario(con, nombre, rol, pin)
    # Recuerda en consola que hay que cambiar esos PIN.
    print(f"AVISO: se crearon usuarios de DEMOSTRACIÓN con PIN {pin}. "
          "Cámbielos con:  python servidor.py cambiar-pin \"Nombre\"")


# ------------------------------------------------------------------ Sesiones
# Sesiones abiertas: token -> datos. Viven en memoria y se pierden al reiniciar el servidor.
SESIONES = {}                 # token -> {"nombre", "rol", "expira"}  (en memoria: se pierden al reiniciar)
# Candado: varios hilos atienden a la vez y no deben tocar el diccionario al mismo tiempo.
SESIONES_LOCK = threading.Lock()
# Una sesión dura 8 horas (un turno de trabajo).
DURACION_SESION = 8 * 3600    # 8 horas: un turno


# Crea una sesión y devuelve su token (va en la cookie).
def abrir_sesion(usuario):
    """Crea una sesión nueva y devuelve su token (va en la cookie). Aprovecha para limpiar las vencidas."""
    # Token aleatorio imposible de adivinar.
    token = secrets.token_urlsafe(32)
    # Toma el candado mientras modifica las sesiones.
    with SESIONES_LOCK:
        # Hora actual en segundos.
        ahora = time.time()
        # Aprovecha para borrar las sesiones ya vencidas.
        for t in [t for t, v in SESIONES.items() if v["expira"] < ahora]:
            # Borra una sesión vencida.
            del SESIONES[t]
        # Guarda nombre, rol y cuándo vence.
        SESIONES[token] = {"nombre": usuario["nombre"], "rol": usuario["rol"], "expira": ahora + DURACION_SESION}
    # Devuelve el token.
    return token


# Devuelve la sesión si sigue vigente; si venció, la borra.
def buscar_sesion(token):
    """Devuelve la sesión de ese token si sigue vigente; si venció, la borra."""
    # Toma el candado.
    with SESIONES_LOCK:
        # Busca el token.
        s = SESIONES.get(token)
        # Si existe y no ha vencido...
        if s and s["expira"] >= time.time():
            # ...devuelve una copia.
            return dict(s)
        # Si no sirve, la elimina (sin error si no existía).
        SESIONES.pop(token, None)
    # Sin sesión válida.
    return None


# Botón Cerrar sesión: elimina la sesión.
def cerrar_sesion(token):
    """Cierra la sesión (botón Cerrar sesión)."""
    # Toma el candado.
    with SESIONES_LOCK:
        # Elimina el token.
        SESIONES.pop(token, None)


# Error propio: la persona entró pero su rol no permite la acción (respuesta 403).
class SinPermiso(Exception):
    """La persona está identificada pero su rol no permite esta acción (respuesta 403)."""
    # No necesita nada más.
    pass


# Corta la petición si el rol no tiene el permiso.
def exigir(sesion, permiso):
    """Corta la petición con SinPermiso si el rol no tiene ese permiso."""
    # Sin sesión o sin permiso...
    if not (sesion and nucleo.puede(sesion["rol"], permiso)):
        # ...se lanza el error de permiso.
        raise SinPermiso()


# Hace una copia consistente de la base aunque se esté usando.
def respaldar(conservar=30):
    """Copia consistente de la base (aunque se esté usando). Guarda las últimas `conservar`."""
    # Crea la carpeta si no existe.
    CARPETA_RESPALDOS.mkdir(exist_ok=True)
    # Nombre con fecha y hora: clinagenda-AAAAMMDD-HHMMSS.db
    destino = CARPETA_RESPALDOS / f"clinagenda-{datetime.now():%Y%m%d-%H%M%S}.db"
    # Abre la base original y el archivo de destino.
    origen, copia = sqlite3.connect(RUTA_DB), sqlite3.connect(destino)
    # Intenta copiar.
    try:
        # Copia segura de SQLite (no sale corrupta aunque alguien escriba en ese momento).
        origen.backup(copia)
    # Pase lo que pase...
    finally:
        # ...cierra la copia...
        copia.close()
        # ...y la original.
        origen.close()
    # Ordena los respaldos y toma los más antiguos (todos menos los últimos 30)...
    for viejo in sorted(CARPETA_RESPALDOS.glob("clinagenda-*.db"))[:-conservar]:
        # ...y los borra.
        viejo.unlink()
    # Devuelve la ruta del respaldo creado.
    return destino


# Hilo de fondo: un respaldo cada 6 horas.
def _respaldo_periodico(cada_horas=6):
    """Hilo de fondo: hace un respaldo cada `cada_horas` horas."""
    # Se repite mientras el servidor esté encendido.
    while True:
        # Espera 6 horas.
        time.sleep(cada_horas * 3600)
        # Intenta respaldar.
        try:
            # Hace la copia.
            respaldar()
        # Si falla...
        except Exception:
            # ...lo muestra en consola sin detener el servidor.
            traceback.print_exc()


# Arma todo lo que ve la pantalla. Cada rol ve solo lo que necesita.
def listar_datos(con, sesion):
    """Cada rol ve solo lo que necesita: recepción no ve correos ni teléfonos y el documento sale
    enmascarado; la doctora ve el detalle completo."""
    # Atajo: ejecuta una consulta y devuelve una lista de diccionarios.
    f = lambda q, *a: [dict(r) for r in con.execute(q, a)]
    # La doctora ve el detalle completo, incluido teléfono, correo y documento...
    if nucleo.puede(sesion["rol"], "ver_contacto"):
        # ...consulta con todos los campos.
        pacientes = f("SELECT id, nombre, fecha_nacimiento, tipo_doc, documento, telefono, correo, "
                      "contacto_responsable FROM pacientes ORDER BY nombre_clave")
    # Recepción: sin teléfono, correo ni contacto...
    else:
        # ...y con el documento enmascarado: '******' más los últimos 4 dígitos.
        pacientes = f("SELECT id, nombre, fecha_nacimiento, tipo_doc, "
                      "CASE WHEN documento IS NULL THEN NULL ELSE '******' || substr(documento, -4) END AS documento "
                      "FROM pacientes ORDER BY nombre_clave")
    # Respuesta única con todos los datos de la pantalla.
    return {
        # Quién es y qué rol tiene (la pantalla ajusta los menús).
        "sesion": {"nombre": sesion["nombre"], "rol": sesion["rol"]},
        # Lista de clínicas.
        "clinicas": f("SELECT id, nombre FROM clinicas ORDER BY id"),
        # Horarios para que la pantalla muestre solo horas válidas.
        "horarios": f("SELECT clinica_id, dia_semana, abre, cierra FROM horarios ORDER BY clinica_id, dia_semana"),
        # Solo médicos activos.
        "medicos": f("SELECT id, nombre FROM medicos WHERE activo=1 ORDER BY nombre"),
        # Pacientes según el rol (arriba).
        "pacientes": pacientes,
        # Citas desde ayer en adelante, máximo 300, con nombres ya unidos (paciente, médico, clínica).
        "citas": f("SELECT c.id, c.inicio, c.fin, c.estado, p.nombre AS paciente, "
                   "m.nombre AS medico, cl.nombre AS clinica "
                   "FROM citas c JOIN pacientes p ON p.id=c.paciente_id "
                   "JOIN medicos m ON m.id=c.medico_id JOIN clinicas cl ON cl.id=c.clinica_id "
                   "WHERE c.inicio >= date('now','localtime','-1 day') "
                   "ORDER BY c.inicio LIMIT 300"),
    }


# Convierte a número o falla con 'Falta elegir: ...'.
def entero(valor, que):
    """Convierte a número o falla con 'Falta elegir: …' para la persona."""
    # Intenta convertir.
    try:
        # Devuelve el número.
        return int(valor)
    # Si venía vacío o con letras...
    except (TypeError, ValueError):
        # ...mensaje para la persona.
        raise ErrorNegocio(f"Falta elegir: {que}.")


# CAMBIO: lo único que se puede usar sin sesión es el login. Antes también estaba /api/usuarios (lista de nombres), que se eliminó.
RUTAS_PUBLICAS = {("POST", "/api/login")}   # lo demás exige sesión


# Atiende cada petición web: la pantalla (GET) y la API /api/... (GET y POST).
class Manejador(BaseHTTPRequestHandler):
    """Atiende cada petición web: sirve la pantalla (GET) y la API /api/... (GET y POST)."""
    # Nombre que el servidor dice ser (no revela la versión de Python).
    server_version = "ClinAgenda"

    # Escribe cada petición en la consola.
    def log_message(self, fmt, *args):
        """Escribe el registro de peticiones en la consola."""
        # Formato: [fecha] petición.
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    # Envía una respuesta con cabeceras de seguridad y, si hay, la cookie de sesión.
    def _responder(self, codigo, datos, tipo="application/json; charset=utf-8"):
        """Envía la respuesta con cabeceras de seguridad y, si corresponde, la cookie de sesión."""
        # Si ya son bytes (archivo), los usa; si no, convierte el diccionario a JSON con tildes.
        cuerpo = datos if isinstance(datos, bytes) else json.dumps(datos, ensure_ascii=False).encode()
        # Código de estado (200, 400, 401...).
        self.send_response(codigo)
        # Tipo de contenido.
        self.send_header("Content-Type", tipo)
        # Tamaño de la respuesta.
        self.send_header("Content-Length", str(len(cuerpo)))
        # El navegador no debe adivinar el tipo de archivo.
        self.send_header("X-Content-Type-Options", "nosniff")
        # Prohíbe mostrar la página dentro de otra (evita clickjacking).
        self.send_header("X-Frame-Options", "DENY")
        # No envía de qué página venía el usuario.
        self.send_header("Referrer-Policy", "no-referrer")
        # Solo permite cargar scripts, estilos e imágenes del propio sitio.
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                         "script-src 'self' 'unsafe-inline'; img-src 'self' data:")
        # El navegador no guarda copias de datos de pacientes.
        self.send_header("Cache-Control", "no-store")
        # Cookie pendiente de enviar (la deja el login o el logout).
        galleta = getattr(self, "_galleta", None)
        # Si hay una...
        if galleta is not None:
            # ...se la envía al navegador.
            self.send_header("Set-Cookie", galleta)
        # Termina las cabeceras.
        self.end_headers()
        # Escribe el contenido de la respuesta.
        self.wfile.write(cuerpo)

    # Lee el token de sesión desde la cookie.
    def _token(self):
        """Lee el token de sesión de la cookie, o None si no hay."""
        # Intenta leer la cookie.
        try:
            # Interpreta el encabezado Cookie.
            c = SimpleCookie(self.headers.get("Cookie") or "")
            # Devuelve el valor de 'sid', o None si no existe.
            return c["sid"].value if "sid" in c else None
        # Una cookie mal formada se trata como si no hubiera.
        except Exception:
            # Sin token.
            return None

    # Atiende los GET: sirve la pantalla y los dibujos permitidos.
    def do_GET(self):
        """Sirve la pantalla y los dibujos permitidos; todo lo demás debe ser /api/..."""
        # Quita los parámetros (?...) de la dirección.
        ruta = self.path.split("?", 1)[0]
        # Si es uno de los archivos permitidos...
        if ruta in ARCHIVOS_WEB:
            # ...busca su nombre real y tipo de contenido...
            nombre, tipo = ARCHIVOS_WEB[ruta]
            # ...y su ubicación en disco.
            archivo = CARPETA_WEB / nombre
            # Si el archivo falta...
            if not archivo.is_file():
                # ...404 con mensaje.
                return self._responder(404, {"error": f"No se encontró {nombre}."})
            # Entrega el archivo.
            return self._responder(200, archivo.read_bytes(), tipo)
        # Cualquier otra ruta que no sea de la API: 404.
        if not ruta.startswith("/api/"):
            # Mensaje de no encontrada.
            return self._responder(404, {"error": "Página no encontrada."})
        # Las rutas /api/ pasan por la puerta de seguridad.
        self._manejar("GET", ruta, {})

    # Atiende los POST: exige JSON y un tamaño razonable.
    def do_POST(self):
        """Exige JSON (frena ataques CSRF) de tamaño razonable y pasa a _manejar."""
        # Tipo de contenido enviado.
        tipo = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        # Un formulario de otra página no puede enviar JSON: esto frena ataques CSRF.
        if tipo != "application/json":   # un formulario de otra página no puede enviar esto: frena CSRF
            # 415 = tipo no aceptado.
            return self._responder(415, {"error": "Solicitud no válida."})
        # Intenta leer el cuerpo.
        try:
            # Tamaño declarado.
            largo = int(self.headers.get("Content-Length") or 0)
            # Un tamaño negativo no tiene sentido...
            if largo < 0:
                # ...se trata como petición mal formada.
                raise ValueError
            # Demasiado grande...
            if largo > MAX_CUERPO:
                # ...se rechaza.
                raise ErrorNegocio("Lo enviado es demasiado grande.")
            # Lee el JSON (vacío = {}).
            datos = json.loads(self.rfile.read(largo) or b"{}")
            # Debe ser un objeto {...}, no una lista ni un número...
            if not isinstance(datos, dict):
                # ...si no, error de formato.
                raise ValueError
        # Si fue demasiado grande...
        except ErrorNegocio as e:
            # ...responde 400 con el mensaje.
            return self._responder(400, {"error": str(e)})
        # Si el JSON está dañado...
        except ValueError:
            # ...responde 400.
            return self._responder(400, {"error": "No se entendió lo enviado. Recargue la página e intente de nuevo."})
        # Todo en orden: pasa a la puerta de seguridad.
        self._manejar("POST", self.path.split("?", 1)[0], datos)

    # Puerta de seguridad: exige sesión (salvo en el login) y ejecuta la acción. (El texto de ayuda menciona 'lista de usuarios': ya no existe.)
    def _manejar(self, metodo, ruta, datos):
        """Puerta de seguridad: exige sesión salvo en login y lista de usuarios, y ejecuta la acción."""
        # Empieza sin cookie pendiente.
        self._galleta = None
        # Busca la sesión del token de la cookie.
        sesion = buscar_sesion(self._token()) if self._token() else None
        # Si la ruta no es pública y no hay sesión válida...
        if (metodo, ruta) not in RUTAS_PUBLICAS and not sesion:
            # ...401: la pantalla muestra el login.
            return self._responder(401, {"error": "Inicie sesión para continuar.", "codigo": "sin_sesion"})
        # Ejecuta la acción dentro de una conexión a la base.
        self._con(lambda con: self._accion(con, metodo, ruta, datos, sesion))

    # Qué hace cada ruta de la API.
    def _accion(self, con, metodo, ruta, datos, sesion):
        """Qué hace cada ruta de la API. El usuario sale SIEMPRE de la sesión, no de lo que envíe el navegador."""
        # Quién actúa sale SIEMPRE de la sesión, nunca de lo que envía el navegador (nadie puede hacerse pasar por otro).
        usuario = sesion["nombre"] if sesion else None   # SIEMPRE sale de la sesión, nunca de lo que envía el navegador
        # Rutas de lectura.
        if metodo == "GET":
            # Datos de la pantalla según el rol.
            if ruta == "/api/datos":
                # Devuelve clínicas, médicos, pacientes y citas.
                return listar_datos(con, sesion)
            # Historial de cambios...
            if ruta == "/api/auditoria":
                # ...solo para quien tenga el permiso (la doctora).
                exigir(sesion, "ver_auditoria")
                # Devuelve los eventos.
                return {"eventos": nucleo.listar_auditoria(con)}
            # Cualquier otra ruta GET no existe. (Aquí cae ahora /api/usuarios.)
            raise ErrorNegocio("Página no encontrada.")

        # Ingreso: usuario y contraseña.
        if ruta == "/api/login":
            # Valida; el campo 'nombre' es lo que escribió y 'pin' es la contraseña.
            u = nucleo.iniciar_sesion(con, datos.get("nombre"), datos.get("pin"))
            # Prepara la cookie: HttpOnly (el JavaScript no la puede leer), SameSite=Strict (no viaja desde otros sitios) y dura 8 horas.
            self._galleta = (f"sid={abrir_sesion(u)}; HttpOnly; SameSite=Strict; Path=/; Max-Age={DURACION_SESION}")
            # Responde con nombre y rol.
            return {"nombre": u["nombre"], "rol": u["rol"]}
        # Cerrar sesión.
        if ruta == "/api/logout":
            # Si había sesión...
            if self._token():
                # ...se elimina.
                cerrar_sesion(self._token())
            # Envía una cookie vacía con duración 0: el navegador la borra.
            self._galleta = "sid=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"
            # Mensaje.
            return {"mensaje": "Sesión cerrada."}
        # Registrar un paciente.
        if ruta == "/api/pacientes":
            # Exige el permiso.
            exigir(sesion, "registrar_paciente")
            # Pasa los datos recibidos; si no hay tipo de documento, 'ninguno'.
            pid = nucleo.registrar_paciente(
                con, usuario, datos.get("nombre"), datos.get("fecha_nacimiento"),
                datos.get("tipo_doc") or "ninguno", datos.get("documento"),
                datos.get("telefono"), datos.get("correo"), datos.get("contacto_responsable"),
                # Verdadero si la persona confirmó que no es un duplicado.
                confirmar_homonimo=bool(datos.get("confirmar_homonimo")))
            # Devuelve el id nuevo.
            return {"id": pid, "mensaje": "Paciente registrado."}
        # Agendar una cita.
        if ruta == "/api/citas":
            # Exige el permiso.
            exigir(sesion, "agendar")
            # Fecha y hora son obligatorias...
            if not datos.get("fecha") or not datos.get("hora"):
                # ...mensaje que marca el campo fecha.
                raise ErrorNegocio("Falta elegir la fecha y la hora de la cita.", campo="fecha")
            # Une fecha y hora: '2026-11-23 09:30'.
            inicio = f"{datos['fecha']} {datos['hora']}".strip()
            # Agenda. 'entero' valida que paciente, médico y clínica sean números; la duración por defecto es 30 minutos; 'clave' evita duplicados al reintentar.
            cid = nucleo.agendar_cita(
                con, usuario, entero(datos.get("paciente_id"), "el paciente"),
                entero(datos.get("medico_id"), "el médico"),
                entero(datos.get("clinica_id"), "la clínica"), inicio,
                entero(datos.get("duracion") or 30, "la duración"), clave=datos.get("clave"))
            # Devuelve el id de la cita.
            return {"id": cid, "mensaje": "Cita agendada."}
        # ¿La ruta es /api/citas/NÚMERO/cancelar o /deshacer?
        m = re.fullmatch(r"/api/citas/(\d+)/(cancelar|deshacer)", ruta)
        # Si coincide...
        if m:
            # ...exige el permiso...
            exigir(sesion, "cancelar")
            # ...elige la función según la palabra final...
            fn = nucleo.cancelar_cita if m.group(2) == "cancelar" else nucleo.deshacer_cancelacion
            # ...la ejecuta con el número de cita y devuelve su mensaje.
            return {"mensaje": fn(con, usuario, int(m.group(1)))}
        # Cualquier otra ruta POST no existe.
        raise ErrorNegocio("Acción no encontrada.")

    # Abre la base, ejecuta la acción y traduce los errores a códigos HTTP.
    def _con(self, fn):
        """Abre la base, ejecuta la acción y traduce los errores: de negocio 400, de permiso 403, inesperados 500 (sin detalles técnicos)."""
        # Aún sin conexión.
        con = None
        # Todo dentro de un try para controlar errores.
        try:
            # Una conexión por petición.
            con = nucleo.conectar(RUTA_DB)
            # Si todo sale bien: 200 con el resultado.
            self._responder(200, fn(con))
        # Error del negocio: 400 con mensaje, código y campo a marcar.
        except ErrorNegocio as e:
            # Respuesta 400.
            self._responder(400, {"error": str(e), "codigo": e.codigo, "campo": e.campo})
        # Falta de permiso: 403.
        except SinPermiso:
            # Respuesta 403.
            self._responder(403, {"error": "Su usuario no tiene permiso para hacer esto.", "codigo": "sin_permiso"})
        # Cualquier error inesperado...
        except Exception:
            # ...el detalle técnico queda en la consola, no en la pantalla...
            traceback.print_exc()  # el detalle técnico queda en la consola, no en la pantalla
            # ...y la persona ve un mensaje amable sin datos técnicos.
            self._responder(500, {"error": "Algo salió mal de nuestro lado. Sus datos no se perdieron; "
                                           "intente de nuevo en un momento."})
        # Pase lo que pase...
        finally:
            # ...si se abrió la conexión...
            if con:
                # ...se cierra.
                con.close()


# Comandos de administración desde la terminal.
def comando_admin(args):
    """python servidor.py usuario-nuevo "Nombre" rol   |   python servidor.py cambiar-pin "Nombre" """
    # Abre la base.
    con = nucleo.conectar(RUTA_DB)
    # Intenta el comando.
    try:
        # Crear usuario: nombre y rol vienen como argumentos.
        if args[0] == "usuario-nuevo" and len(args) == 3:
            # Pide el PIN sin mostrarlo en pantalla.
            pin = getpass.getpass("PIN (4 a 12 números): ")
            # Guarda el usuario.
            nucleo.crear_usuario(con, args[1], args[2], pin)
            # Confirmación.
            print("Usuario creado.")
        # Cambiar PIN de un usuario.
        elif args[0] == "cambiar-pin" and len(args) == 2:
            # Pide el PIN nuevo y lo guarda.
            nucleo.cambiar_pin(con, args[1], getpass.getpass("PIN nuevo (4 a 12 números): "))
            # Confirmación.
            print("PIN cambiado.")
        # Comando desconocido...
        else:
            # ...muestra la ayuda.
            print(__doc__)
    # Error de negocio...
    except ErrorNegocio as e:
        # ...se muestra el mensaje.
        print("No se pudo:", e)
    # Siempre...
    finally:
        # ...cierra la base.
        con.close()


# Arranca todo.
def main():
    """Arranca: prepara la base, hace un respaldo, lanza el hilo de respaldos y escucha peticiones."""
    # Si hay argumentos, es un comando de administración...
    if len(sys.argv) > 1:
        # ...se ejecuta y termina.
        return comando_admin(sys.argv[1:])
    # Puerto (8000 por defecto).
    puerto = int(os.environ.get("PORT", 8000))
    # 127.0.0.1 = solo este equipo. Con 0.0.0.0 se abre a la red local.
    host = os.environ.get("CLINAGENDA_HOST", "127.0.0.1")
    # Crea tablas y datos de demostración si hace falta.
    preparar_base()
    # Intenta el respaldo inicial.
    try:
        # Copia al arrancar.
        respaldar()   # copia al arrancar; luego una cada 6 horas
    # Si falla, no impide arrancar.
    except Exception:
        # Muestra el error.
        traceback.print_exc()
    # Lanza el hilo de respaldos cada 6 horas (daemon: se apaga con el servidor).
    threading.Thread(target=_respaldo_periodico, daemon=True).start()
    # Avisa la dirección.
    print(f"ClinAgenda lista en http://localhost:{puerto}  (Ctrl+C para detener)")
    # Empieza a atender peticiones (un hilo por petición) hasta Ctrl+C.
    ThreadingHTTPServer((host, puerto), Manejador).serve_forever()


# Solo arranca si se ejecuta este archivo directamente (no al importarlo desde las pruebas).
if __name__ == "__main__":
    # Llama a la función principal.
    main()
