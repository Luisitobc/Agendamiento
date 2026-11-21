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
import getpass
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
import traceback
from datetime import datetime
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Estructura: <raiz>/Servidor/servidor.py, <raiz>/Codigos_Python/nucleo.py, <raiz>/estructura_html/, <raiz>/MSLS.db
RAIZ_SERVIDOR = Path(__file__).resolve().parent
RAIZ = RAIZ_SERVIDOR.parent
for _ruta in (RAIZ / "Codigos_Python", RAIZ_SERVIDOR):
    if str(_ruta) not in sys.path:
        sys.path.insert(0, str(_ruta))

import nucleo  # noqa: E402  (necesita el sys.path de arriba)
from nucleo import ErrorNegocio  # noqa: E402

CARPETA_WEB = RAIZ / "estructura_html"
RUTA_DB = os.environ.get("MSLS_DB", str(RAIZ / "MSLS.db"))
CARPETA_RESPALDOS = RAIZ / "respaldos"
MAX_CUERPO = 100_000

# Solo se sirven estos archivos (nada de rutas arbitrarias del disco).
ARCHIVOS_WEB = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/fondo_suave.svg": ("fondo_suave.svg", "image/svg+xml"),
    "/ilustracion_clinica.svg": ("ilustracion_clinica.svg", "image/svg+xml"),
}


def preparar_base():
    """Crea las tablas y, si la base está vacía, datos INVENTADOS de demostración."""
    con = nucleo.conectar(RUTA_DB)
    if not con.execute("SELECT 1 FROM clinicas").fetchone():
        con.execute("INSERT INTO clinicas (nombre) VALUES ('Clínica Centro'),('Clínica Norte'),('Clínica Sur')")
        con.execute("INSERT INTO medicos (nombre) VALUES ('Dr. Salazar'),('Dra. Mendoza'),('Dr. Vega'),('Dra. Castillo')")
        for nombre, nac in [("Juan Pérez", "1980-05-10"), ("María López", "1975-11-02"),
                            ("Pedro Andrade", "2016-08-21"), ("Carmen Ruiz", "1948-03-14")]:
            nucleo.registrar_paciente(con, "sistema", nombre, nac)
    sembrar_horarios(con)
    sembrar_usuarios(con)
    con.close()


def sembrar_horarios(con):
    """Horarios INVENTADOS de demostración para cada clínica que aún no tenga uno.
    La clienta debe confirmarlos (ver DECISIONES.md)."""
    for c in con.execute("SELECT id, nombre FROM clinicas").fetchall():
        if con.execute("SELECT 1 FROM horarios WHERE clinica_id=?", (c["id"],)).fetchone():
            continue
        sur = "sur" in c["nombre"].lower()
        for dia in range(5):   # lunes a viernes
            con.execute("INSERT INTO horarios VALUES (?,?,?,?)", (c["id"], dia, "08:00", "13:00" if sur else "17:00"))
        if not sur:
            con.execute("INSERT INTO horarios VALUES (?,?,?,?)", (c["id"], 5, "08:00", "12:00"))   # sábado


def sembrar_usuarios(con):
    """Solo si no hay ningún usuario: crea los de DEMOSTRACIÓN. Cambiar los PIN antes de usarlo de verdad."""
    if con.execute("SELECT 1 FROM usuarios").fetchone():
        return
    pin = os.environ.get("CLINAGENDA_PIN_DEMO", "1234")
    for nombre, rol in [("Rosa (recepción)", "recepcion"), ("Recepción Sur", "recepcion"),
                        ("Dra. Paredes", "doctora")]:
        nucleo.crear_usuario(con, nombre, rol, pin)
    print(f"AVISO: se crearon usuarios de DEMOSTRACIÓN con PIN {pin}. "
          "Cámbielos con:  python servidor.py cambiar-pin \"Nombre\"")


# ------------------------------------------------------------------ Sesiones
SESIONES = {}                 # token -> {"nombre", "rol", "expira"}  (en memoria: se pierden al reiniciar)
SESIONES_LOCK = threading.Lock()
DURACION_SESION = 8 * 3600    # 8 horas: un turno


def abrir_sesion(usuario):
    """Crea una sesión nueva y devuelve su token (va en la cookie). Aprovecha para limpiar las vencidas."""
    token = secrets.token_urlsafe(32)
    with SESIONES_LOCK:
        ahora = time.time()
        for t in [t for t, v in SESIONES.items() if v["expira"] < ahora]:
            del SESIONES[t]
        SESIONES[token] = {"nombre": usuario["nombre"], "rol": usuario["rol"], "expira": ahora + DURACION_SESION}
    return token


def buscar_sesion(token):
    """Devuelve la sesión de ese token si sigue vigente; si venció, la borra."""
    with SESIONES_LOCK:
        s = SESIONES.get(token)
        if s and s["expira"] >= time.time():
            return dict(s)
        SESIONES.pop(token, None)
    return None


def cerrar_sesion(token):
    """Cierra la sesión (botón Cerrar sesión)."""
    with SESIONES_LOCK:
        SESIONES.pop(token, None)


class SinPermiso(Exception):
    """La persona está identificada pero su rol no permite esta acción (respuesta 403)."""
    pass


def exigir(sesion, permiso):
    """Corta la petición con SinPermiso si el rol no tiene ese permiso."""
    if not (sesion and nucleo.puede(sesion["rol"], permiso)):
        raise SinPermiso()


def respaldar(conservar=30):
    """Copia consistente de la base (aunque se esté usando). Guarda las últimas `conservar`."""
    CARPETA_RESPALDOS.mkdir(exist_ok=True)
    destino = CARPETA_RESPALDOS / f"clinagenda-{datetime.now():%Y%m%d-%H%M%S}.db"
    origen, copia = sqlite3.connect(RUTA_DB), sqlite3.connect(destino)
    try:
        origen.backup(copia)
    finally:
        copia.close()
        origen.close()
    for viejo in sorted(CARPETA_RESPALDOS.glob("clinagenda-*.db"))[:-conservar]:
        viejo.unlink()
    return destino


def _respaldo_periodico(cada_horas=6):
    """Hilo de fondo: hace un respaldo cada `cada_horas` horas."""
    while True:
        time.sleep(cada_horas * 3600)
        try:
            respaldar()
        except Exception:
            traceback.print_exc()


def listar_datos(con, sesion):
    """Cada rol ve solo lo que necesita: recepción no ve correos ni teléfonos y el documento sale
    enmascarado; la doctora ve el detalle completo."""
    f = lambda q, *a: [dict(r) for r in con.execute(q, a)]
    if nucleo.puede(sesion["rol"], "ver_contacto"):
        pacientes = f("SELECT id, nombre, fecha_nacimiento, tipo_doc, documento, telefono, correo, "
                      "contacto_responsable FROM pacientes ORDER BY nombre_clave")
    else:
        pacientes = f("SELECT id, nombre, fecha_nacimiento, tipo_doc, "
                      "CASE WHEN documento IS NULL THEN NULL ELSE '******' || substr(documento, -4) END AS documento "
                      "FROM pacientes ORDER BY nombre_clave")
    return {
        "sesion": {"nombre": sesion["nombre"], "rol": sesion["rol"]},
        "clinicas": f("SELECT id, nombre FROM clinicas ORDER BY id"),
        "horarios": f("SELECT clinica_id, dia_semana, abre, cierra FROM horarios ORDER BY clinica_id, dia_semana"),
        "medicos": f("SELECT id, nombre FROM medicos WHERE activo=1 ORDER BY nombre"),
        "pacientes": pacientes,
        "citas": f("SELECT c.id, c.inicio, c.fin, c.estado, p.nombre AS paciente, "
                   "m.nombre AS medico, cl.nombre AS clinica "
                   "FROM citas c JOIN pacientes p ON p.id=c.paciente_id "
                   "JOIN medicos m ON m.id=c.medico_id JOIN clinicas cl ON cl.id=c.clinica_id "
                   "WHERE c.inicio >= date('now','localtime','-1 day') "
                   "ORDER BY c.inicio LIMIT 300"),
    }


def entero(valor, que):
    """Convierte a número o falla con 'Falta elegir: …' para la persona."""
    try:
        return int(valor)
    except (TypeError, ValueError):
        raise ErrorNegocio(f"Falta elegir: {que}.")


RUTAS_PUBLICAS = {("GET", "/api/usuarios"), ("POST", "/api/login")}   # lo demás exige sesión


class Manejador(BaseHTTPRequestHandler):
    """Atiende cada petición web: sirve la pantalla (GET) y la API /api/... (GET y POST)."""
    server_version = "ClinAgenda"

    def log_message(self, fmt, *args):
        """Escribe el registro de peticiones en la consola."""
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def _responder(self, codigo, datos, tipo="application/json; charset=utf-8"):
        """Envía la respuesta con cabeceras de seguridad y, si corresponde, la cookie de sesión."""
        cuerpo = datos if isinstance(datos, bytes) else json.dumps(datos, ensure_ascii=False).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                         "script-src 'self' 'unsafe-inline'; img-src 'self' data:")
        self.send_header("Cache-Control", "no-store")
        galleta = getattr(self, "_galleta", None)
        if galleta is not None:
            self.send_header("Set-Cookie", galleta)
        self.end_headers()
        self.wfile.write(cuerpo)

    def _token(self):
        """Lee el token de sesión de la cookie, o None si no hay."""
        try:
            c = SimpleCookie(self.headers.get("Cookie") or "")
            return c["sid"].value if "sid" in c else None
        except Exception:
            return None

    def do_GET(self):
        """Sirve la pantalla y los dibujos permitidos; todo lo demás debe ser /api/..."""
        ruta = self.path.split("?", 1)[0]
        if ruta in ARCHIVOS_WEB:
            nombre, tipo = ARCHIVOS_WEB[ruta]
            archivo = CARPETA_WEB / nombre
            if not archivo.is_file():
                return self._responder(404, {"error": f"No se encontró {nombre}."})
            return self._responder(200, archivo.read_bytes(), tipo)
        if not ruta.startswith("/api/"):
            return self._responder(404, {"error": "Página no encontrada."})
        self._manejar("GET", ruta, {})

    def do_POST(self):
        """Exige JSON (frena ataques CSRF) de tamaño razonable y pasa a _manejar."""
        tipo = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if tipo != "application/json":   # un formulario de otra página no puede enviar esto: frena CSRF
            return self._responder(415, {"error": "Solicitud no válida."})
        try:
            largo = int(self.headers.get("Content-Length") or 0)
            if largo < 0:
                raise ValueError
            if largo > MAX_CUERPO:
                raise ErrorNegocio("Lo enviado es demasiado grande.")
            datos = json.loads(self.rfile.read(largo) or b"{}")
            if not isinstance(datos, dict):
                raise ValueError
        except ErrorNegocio as e:
            return self._responder(400, {"error": str(e)})
        except ValueError:
            return self._responder(400, {"error": "No se entendió lo enviado. Recargue la página e intente de nuevo."})
        self._manejar("POST", self.path.split("?", 1)[0], datos)

    def _manejar(self, metodo, ruta, datos):
        """Puerta de seguridad: exige sesión salvo en login y lista de usuarios, y ejecuta la acción."""
        self._galleta = None
        sesion = buscar_sesion(self._token()) if self._token() else None
        if (metodo, ruta) not in RUTAS_PUBLICAS and not sesion:
            return self._responder(401, {"error": "Inicie sesión para continuar.", "codigo": "sin_sesion"})
        self._con(lambda con: self._accion(con, metodo, ruta, datos, sesion))

    def _accion(self, con, metodo, ruta, datos, sesion):
        """Qué hace cada ruta de la API. El usuario sale SIEMPRE de la sesión, no de lo que envíe el navegador."""
        usuario = sesion["nombre"] if sesion else None   # SIEMPRE sale de la sesión, nunca de lo que envía el navegador
        if metodo == "GET":
            if ruta == "/api/usuarios":
                return {"usuarios": [r["nombre"] for r in con.execute(
                    "SELECT nombre FROM usuarios WHERE activo=1 ORDER BY nombre")]}
            if ruta == "/api/datos":
                return listar_datos(con, sesion)
            if ruta == "/api/auditoria":
                exigir(sesion, "ver_auditoria")
                return {"eventos": nucleo.listar_auditoria(con)}
            raise ErrorNegocio("Página no encontrada.")

        if ruta == "/api/login":
            u = nucleo.iniciar_sesion(con, datos.get("nombre"), datos.get("pin"))
            self._galleta = (f"sid={abrir_sesion(u)}; HttpOnly; SameSite=Strict; Path=/; Max-Age={DURACION_SESION}")
            return {"nombre": u["nombre"], "rol": u["rol"]}
        if ruta == "/api/logout":
            if self._token():
                cerrar_sesion(self._token())
            self._galleta = "sid=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"
            return {"mensaje": "Sesión cerrada."}
        if ruta == "/api/pacientes":
            exigir(sesion, "registrar_paciente")
            pid = nucleo.registrar_paciente(
                con, usuario, datos.get("nombre"), datos.get("fecha_nacimiento"),
                datos.get("tipo_doc") or "ninguno", datos.get("documento"),
                datos.get("telefono"), datos.get("correo"), datos.get("contacto_responsable"),
                confirmar_homonimo=bool(datos.get("confirmar_homonimo")))
            return {"id": pid, "mensaje": "Paciente registrado."}
        if ruta == "/api/citas":
            exigir(sesion, "agendar")
            if not datos.get("fecha") or not datos.get("hora"):
                raise ErrorNegocio("Falta elegir la fecha y la hora de la cita.", campo="fecha")
            inicio = f"{datos['fecha']} {datos['hora']}".strip()
            cid = nucleo.agendar_cita(
                con, usuario, entero(datos.get("paciente_id"), "el paciente"),
                entero(datos.get("medico_id"), "el médico"),
                entero(datos.get("clinica_id"), "la clínica"), inicio,
                entero(datos.get("duracion") or 30, "la duración"), clave=datos.get("clave"))
            return {"id": cid, "mensaje": "Cita agendada."}
        m = re.fullmatch(r"/api/citas/(\d+)/(cancelar|deshacer)", ruta)
        if m:
            exigir(sesion, "cancelar")
            fn = nucleo.cancelar_cita if m.group(2) == "cancelar" else nucleo.deshacer_cancelacion
            return {"mensaje": fn(con, usuario, int(m.group(1)))}
        raise ErrorNegocio("Acción no encontrada.")

    def _con(self, fn):
        """Abre la base, ejecuta la acción y traduce los errores: de negocio 400, de permiso 403, inesperados 500 (sin detalles técnicos)."""
        con = None
        try:
            con = nucleo.conectar(RUTA_DB)
            self._responder(200, fn(con))
        except ErrorNegocio as e:
            self._responder(400, {"error": str(e), "codigo": e.codigo, "campo": e.campo})
        except SinPermiso:
            self._responder(403, {"error": "Su usuario no tiene permiso para hacer esto.", "codigo": "sin_permiso"})
        except Exception:
            traceback.print_exc()  # el detalle técnico queda en la consola, no en la pantalla
            self._responder(500, {"error": "Algo salió mal de nuestro lado. Sus datos no se perdieron; "
                                           "intente de nuevo en un momento."})
        finally:
            if con:
                con.close()


def comando_admin(args):
    """python servidor.py usuario-nuevo "Nombre" rol   |   python servidor.py cambiar-pin "Nombre" """
    con = nucleo.conectar(RUTA_DB)
    try:
        if args[0] == "usuario-nuevo" and len(args) == 3:
            pin = getpass.getpass("PIN (4 a 12 números): ")
            nucleo.crear_usuario(con, args[1], args[2], pin)
            print("Usuario creado.")
        elif args[0] == "cambiar-pin" and len(args) == 2:
            nucleo.cambiar_pin(con, args[1], getpass.getpass("PIN nuevo (4 a 12 números): "))
            print("PIN cambiado.")
        else:
            print(__doc__)
    except ErrorNegocio as e:
        print("No se pudo:", e)
    finally:
        con.close()


def main():
    """Arranca: prepara la base, hace un respaldo, lanza el hilo de respaldos y escucha peticiones."""
    if len(sys.argv) > 1:
        return comando_admin(sys.argv[1:])
    puerto = int(os.environ.get("PORT", 8000))
    host = os.environ.get("CLINAGENDA_HOST", "127.0.0.1")
    preparar_base()
    try:
        respaldar()   # copia al arrancar; luego una cada 6 horas
    except Exception:
        traceback.print_exc()
    threading.Thread(target=_respaldo_periodico, daemon=True).start()
    print(f"ClinAgenda lista en http://localhost:{puerto}  (Ctrl+C para detener)")
    ThreadingHTTPServer((host, puerto), Manejador).serve_forever()


if __name__ == "__main__":
    main()
