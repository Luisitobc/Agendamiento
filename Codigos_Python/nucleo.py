"""ClinAgenda - núcleo (parte 1): datos limpios y citas que no se cruzan.

Solo usa la biblioteca estándar de Python (sqlite3), sin instalar nada.
Las horas se guardan como hora local sin zona horaria (Ecuador no tiene
cambio de horario). Ver DECISIONES.md.
"""
# hashlib: calcula la huella (scrypt) del PIN.
import hashlib
# hmac: compara huellas en tiempo constante.
import hmac
# os: genera bytes aleatorios para la sal.
import os
# re: expresiones regulares para validar textos.
import re
# sqlite3: la base de datos (viene con Python, no se instala).
import sqlite3
# unicodedata: separa las tildes de las letras.
import unicodedata
# contextmanager: permite escribir 'with transaccion(con):'.
from contextlib import contextmanager
# Tipos de fecha y hora que se usan en todo el archivo.
from datetime import datetime, timedelta, date


# Error 'normal' del negocio: su mensaje se le muestra tal cual a la persona.
class ErrorNegocio(Exception):
    """Error pensado para mostrarse tal cual a una persona (sin jerga técnica).
    `codigo` permite a la interfaz ofrecer una acción (ej. 'homonimo')."""

    # Guarda el mensaje, un código opcional (para que la pantalla ofrezca una acción) y el campo con problema.
    def __init__(self, mensaje, codigo=None, campo=None):
        """`mensaje` es para la persona; `codigo` y `campo` permiten que la pantalla actúe y marque el casillero."""
        # Pasa el mensaje a la clase Exception.
        super().__init__(mensaje)
        # Código opcional, por ejemplo 'homonimo'.
        self.codigo = codigo
        # Nombre del casillero a marcar en la pantalla.
        self.campo = campo   # qué casillero está mal, para que la pantalla lo marque


# ----------------------------------------------------------------- Base de datos
# Todas las tablas de la base de datos. Se ejecuta en cada conexión; IF NOT EXISTS evita duplicarlas.
ESQUEMA = """
-- Activa las llaves foráneas: no se puede citar a un paciente que no existe.
PRAGMA foreign_keys = ON;

-- Tabla de clínicas (Centro, Norte, Sur).
CREATE TABLE IF NOT EXISTS clinicas (
    id INTEGER PRIMARY KEY, nombre TEXT NOT NULL UNIQUE);

-- Tabla de médicos; 'activo' = 0 significa dado de baja.
CREATE TABLE IF NOT EXISTS medicos (
    id INTEGER PRIMARY KEY, nombre TEXT NOT NULL, activo INTEGER NOT NULL DEFAULT 1);

-- Tabla de pacientes. Guarda el nombre bonito y otra versión 'clave' (sin tildes ni mayúsculas) para comparar.
CREATE TABLE IF NOT EXISTS pacientes (
    id INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL,            -- como se muestra: "María López"
    nombre_clave TEXT NOT NULL,      -- para comparar: "maria lopez"
    fecha_nacimiento TEXT NOT NULL,
    tipo_doc TEXT NOT NULL CHECK (tipo_doc IN ('cedula','pasaporte','ninguno')),
    documento TEXT,
    telefono TEXT,
    correo TEXT,
    contacto_responsable TEXT,       -- tutor (niños) o familiar, si no hay teléfono propio
    creado_en TEXT NOT NULL);

-- Un mismo documento no puede pertenecer a dos pacientes.
-- Índice único: la base impide que dos pacientes tengan el mismo documento.
CREATE UNIQUE INDEX IF NOT EXISTS ux_paciente_doc
    ON pacientes (tipo_doc, documento) WHERE documento IS NOT NULL;

-- Tabla de citas. 'estado' solo puede ser activa o cancelada; 'clave' evita duplicados al reintentar.
CREATE TABLE IF NOT EXISTS citas (
    id INTEGER PRIMARY KEY,
    paciente_id INTEGER NOT NULL REFERENCES pacientes(id),
    medico_id INTEGER NOT NULL REFERENCES medicos(id),
    clinica_id INTEGER NOT NULL REFERENCES clinicas(id),
    inicio TEXT NOT NULL,
    fin TEXT NOT NULL,
    estado TEXT NOT NULL DEFAULT 'activa' CHECK (estado IN ('activa','cancelada')),
    clave TEXT,                      -- identifica el intento: un reintento no duplica la cita
    creado_por TEXT NOT NULL,
    creado_en TEXT NOT NULL);

-- Red de seguridad en la base de datos: aunque falle el código, el mismo
-- médico no puede tener dos citas ACTIVAS a la misma hora. Al cancelar, la
-- hora queda libre porque la cita deja de ser 'activa'.
-- Red de seguridad: un médico no puede tener dos citas activas a la misma hora, aunque falle el código.
CREATE UNIQUE INDEX IF NOT EXISTS ux_medico_hora_activa
    ON citas (medico_id, inicio) WHERE estado = 'activa';

-- Quién hizo qué y cuándo. Nunca se borra.
-- Historial de quién hizo qué y cuándo. Nunca se borra.
CREATE TABLE IF NOT EXISTS auditoria (
    id INTEGER PRIMARY KEY,
    cuando TEXT NOT NULL, usuario TEXT NOT NULL, accion TEXT NOT NULL,
    entidad TEXT NOT NULL, entidad_id INTEGER, detalle TEXT);

-- Horario de atención de cada clínica (0 = lunes ... 6 = domingo). Sin fila = cerrada ese día.
-- Horario de cada clínica por día (0 = lunes ... 6 = domingo).
CREATE TABLE IF NOT EXISTS horarios (
    clinica_id INTEGER NOT NULL REFERENCES clinicas(id),
    dia_semana INTEGER NOT NULL CHECK (dia_semana BETWEEN 0 AND 6),
    abre TEXT NOT NULL, cierra TEXT NOT NULL,
    PRIMARY KEY (clinica_id, dia_semana));

-- Personas que usan el sistema. El PIN nunca se guarda: solo su huella (scrypt con sal).
-- Usuarios del sistema. Del PIN solo se guarda su huella; 'intentos' y 'bloqueado_hasta' controlan el bloqueo.
CREATE TABLE IF NOT EXISTS usuarios (
    id INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL UNIQUE,
    rol TEXT NOT NULL CHECK (rol IN ('recepcion','doctora')),
    pin_hash TEXT NOT NULL,
    intentos INTEGER NOT NULL DEFAULT 0,
    bloqueado_hasta TEXT,
    activo INTEGER NOT NULL DEFAULT 1);
"""


# Abre (o crea) la base de datos y la deja lista. ':memory:' = base temporal para pruebas.
def conectar(ruta=":memory:"):
    """Abre la base ( crea o actualiza si hace falta) y devuelve la conexión. Se usa una conexión por petición."""
    # Conecta. isolation_level=None: las transacciones las controlamos nosotros. timeout=10: espera hasta 10 s si otra persona está escribiendo.
    con = sqlite3.connect(ruta, isolation_level=None, timeout=10)  # transacciones manuales
    # Permite leer las columnas por nombre: fila['nombre'].
    con.row_factory = sqlite3.Row
    # Modo WAL: leer no bloquea a escribir y es más seguro ante cortes de luz.
    con.execute("PRAGMA journal_mode = WAL")   # leer no bloquea a escribir; más seguro ante cortes
    # Crea las tablas si no existen.
    con.executescript(ESQUEMA)
    # Migración: si la base es antigua y no tiene la columna 'clave', se la agrega.
    if "clave" not in [r[1] for r in con.execute("PRAGMA table_info(citas)")]:
        # Agrega la columna que falta.
        con.execute("ALTER TABLE citas ADD COLUMN clave TEXT")   # bases creadas antes de esta versión
    # Impide dos citas con la misma clave (reintentos).
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_cita_clave ON citas (clave) WHERE clave IS NOT NULL")
    # Devuelve la conexión lista para usar.
    return con


# Convierte la función de abajo en un bloque 'with'.
@contextmanager
# Agrupa varios cambios: o se guardan todos o no se guarda ninguno.
def transaccion(con):
    """BEGIN IMMEDIATE bloquea a otros escritores: dos recepcionistas en el
    mismo segundo se atienden una tras otra, no a la vez."""
    # Empieza reservando el derecho a escribir: dos recepcionistas a la vez se atienden una tras otra.
    con.execute("BEGIN IMMEDIATE")
    # Todo lo que ocurra dentro del 'with' queda protegido.
    try:
        # Aquí se ejecuta el código del bloque 'with'.
        yield
        # Si no hubo errores, se guardan los cambios.
        con.execute("COMMIT")
    # Si hubo cualquier error (incluso interrupciones)...
    except BaseException:
        # ...se deshace todo lo hecho dentro del bloque.
        con.execute("ROLLBACK")
        # Vuelve a lanzar el error para que quien llamó se entere.
        raise


# Escribe una línea en el historial. Se llama dentro de la misma transacción que el cambio.
def _auditar(con, usuario, accion, entidad, entidad_id, detalle=""):
    """Anota en el historial quién hizo qué y cuándo (dentro de la misma transacción que el cambio)."""
    # Inserta la fila de auditoría con 6 datos: cuándo, quién, acción, qué entidad, su id y detalle.
    con.execute(
        "INSERT INTO auditoria (cuando, usuario, accion, entidad, entidad_id, detalle)"
        " VALUES (?,?,?,?,?,?)",
        # Fecha y hora actual, sin fracciones de segundo.
        (datetime.now().isoformat(timespec="seconds"), usuario, accion,
         entidad, entidad_id, detalle))


# Validaciones
# Deja un solo espacio entre palabras y quita los de los extremos.
def normalizar_espacios(texto):
    """Quita espacios de más: '  Ana   Gil ' queda 'Ana Gil'."""
    # \s+ = uno o más espacios/tabs; (texto or "") evita fallar con None.
    return re.sub(r"\s+", " ", (texto or "")).strip()


# Quita las tildes: 'María' -> 'Maria'.
def _sin_tildes(texto):
    """Quita las tildes para poder comparar nombres."""
    # NFD separa la letra de su tilde; se descartan las marcas (categoría 'Mn') y se une el resto.
    return "".join(c for c in unicodedata.normalize("NFD", texto)
                   if unicodedata.category(c) != "Mn")


# Versión comparable de un nombre: sin espacios dobles, sin tildes y en minúsculas.
def clave_nombre(nombre):
    """'  MARÍA   lópez ' y 'Maria Lopez' dan la misma clave: 'maria lopez'."""
    # casefold() es una minúscula más completa que lower().
    return _sin_tildes(normalizar_espacios(nombre)).casefold()


# Palabras que van en minúscula dentro de un nombre ('de', 'los'...).
_PARTICULAS = {"de", "del", "la", "las", "los", "y", "da", "di"}


# Pone mayúscula inicial a cada palabra excepto a las partículas.
def _titulo(nombre):
    """'maría DE los ángeles' -> 'María de los Ángeles' (str.title() escribiría 'De Los')."""
    # Todo a minúsculas y separado por palabras.
    palabras = nombre.lower().split(" ")
    # La primera palabra siempre lleva mayúscula; las partículas intermedias quedan en minúscula.
    return " ".join(p if i > 0 and p in _PARTICULAS else p.title()
                    for i, p in enumerate(palabras))


# Valida el nombre y lo devuelve bien escrito.
def limpiar_nombre(nombre):
    """Valida y deja el nombre presentable. Rechaza emojis, símbolos y código."""
    # Limpia los espacios.
    nombre = normalizar_espacios(nombre)
    # Exige al menos nombre y apellido (debe haber un espacio).
    if len(nombre) < 3 or " " not in nombre:
        # Mensaje para la persona.
        raise ErrorNegocio("Escriba el nombre completo del paciente (nombre y apellido).")
    # Rechaza textos larguísimos (por ejemplo, algo pegado por error).
    if len(nombre) > 100:
        # Mensaje de nombre largo.
        raise ErrorNegocio("El nombre es demasiado largo. Revise que no se haya pegado texto de más.")
    # Solo letras (con tildes), y entre palabras espacio, apóstrofo, guion o punto. Rechaza números, emojis y código.
    if not re.fullmatch(r"[^\W\d_]+(?:[ '\-.][^\W\d_]+)*\.?", nombre):
        # Mensaje de caracteres no permitidos.
        raise ErrorNegocio("El nombre solo puede tener letras y espacios. "
                           "Quite números, símbolos o emojis.")
    # Devuelve el nombre con mayúsculas correctas.
    return _titulo(nombre)


# Valida una cédula ecuatoriana con su dígito verificador.
def validar_cedula(cedula):
    """Cédula ecuatoriana: 10 dígitos, provincia 01-24 o 30, tercer dígito < 6
    y dígito verificador correcto (módulo 10)."""
    # Quita espacios de los extremos (None se trata como vacío).
    c = (cedula or "").strip()
    # Solo dígitos 0-9 (str.isdigit() también acepta '²' o dígitos árabes y luego fallaría int()).
    # Solo dígitos 0-9 (isdigit() aceptaría '²' o números árabes y luego fallaría).
    if not re.fullmatch(r"[0-9]+", c):
        # Mensaje de caracteres no permitidos.
        raise ErrorNegocio("La cédula solo debe tener números, sin letras ni espacios.")
    # Debe tener exactamente 10 dígitos.
    if len(c) != 10:
        # Indica cuántos dígitos escribió.
        raise ErrorNegocio(f"La cédula debe tener 10 dígitos (escribió {len(c)}).")
    # Los dos primeros dígitos son la provincia.
    provincia = int(c[:2])
    # Provincia 01-24 o 30; el tercer dígito debe ser menor que 6; y no puede ser todo el mismo número (1111111111).
    if not (1 <= provincia <= 24 or provincia == 30) or int(c[2]) >= 6 or len(set(c)) == 1:
        # Mensaje de inicio inválido.
        raise ErrorNegocio("Esa cédula no parece válida. Revise los primeros dígitos.")
    # Acumulador del algoritmo módulo 10.
    total = 0
    # Recorre los 9 primeros dígitos con sus coeficientes alternados 2,1,2,1...
    for d, coef in zip(c[:9], [2, 1, 2, 1, 2, 1, 2, 1, 2]):
        # Multiplica el dígito por su coeficiente.
        p = int(d) * coef
        # Si el producto pasa de 9, se le resta 9 (equivale a sumar sus dígitos).
        total += p - 9 if p > 9 else p
    # Dígito que debería tener la cédula al final.
    verificador = (10 - total % 10) % 10
    # Si no coincide con el décimo dígito, la cédula está mal escrita.
    if verificador != int(c[9]):
        # Mensaje de dígito verificador incorrecto.
        raise ErrorNegocio("Esa cédula no es válida: el último dígito no coincide. "
                           "Revise que esté bien escrita.")
    # Cédula correcta.
    return c


# Valida según el tipo: cédula, pasaporte o ninguno.
def validar_documento(tipo_doc, documento):
    """Devuelve (tipo, documento) listos para guardar."""
    # Sin documento: se guarda vacío.
    if tipo_doc == "ninguno":
        return "ninguno", None
    # Limpia los espacios.
    documento = (documento or "").strip()
    # Para cédula usa la validación completa de arriba.
    if tipo_doc == "cedula":
        return "cedula", validar_cedula(documento)
    # Pasaporte: se pasa a mayúsculas...
    if tipo_doc == "pasaporte":
        documento = documento.upper()
        # ...y debe tener entre 5 y 15 letras o números.
        if not re.fullmatch(r"[A-Z0-9]{5,15}", documento):
            # Mensaje de pasaporte inválido.
            raise ErrorNegocio("El pasaporte debe tener entre 5 y 15 letras o números, sin espacios.")
        # Pasaporte correcto.
        return "pasaporte", documento
    # Tipo de documento desconocido.
    raise ErrorNegocio("Elija si el paciente tiene cédula, pasaporte o ningún documento.")


# Teléfono opcional de Ecuador.
def validar_telefono(telefono):
    """Opcional. Celular 09XXXXXXXX o fijo 0[2-7]XXXXXXX (Ecuador)."""
    # Si está vacío, no es error: devuelve None.
    if not telefono or not telefono.strip():
        return None
    # Quita espacios, guiones y paréntesis.
    t = re.sub(r"[\s\-()]", "", telefono)
    # Convierte el formato internacional +593... al local 0...
    if t.startswith("+593"):
        t = "0" + t[4:]
    # Celular: 09 + 8 dígitos; fijo: 0 + dígito 2 a 7 + 7 dígitos.
    if re.fullmatch(r"09\d{8}", t) or re.fullmatch(r"0[2-7]\d{7}", t):
        return t
    # Mensaje con un ejemplo.
    raise ErrorNegocio("El teléfono no parece correcto. Un celular tiene 10 dígitos "
                       "y empieza con 09 (ejemplo: 0991234567).")


# Correo opcional.
def validar_correo(correo):
    """Opcional."""
    # Vacío = permitido.
    if not correo or not correo.strip():
        return None
    # Se guarda en minúsculas y sin espacios.
    c = correo.strip().lower()
    # Forma básica: algo@dominio.ext.
    if not re.fullmatch(r"[a-z0-9._%+\-]+@[a-z0-9\-]+(\.[a-z0-9\-]+)*\.[a-z]{2,}", c):
        # Mensaje con ejemplo.
        raise ErrorNegocio("El correo no parece correcto. Debe verse así: nombre@correo.com")
    return c


# Valida la fecha de nacimiento.
def validar_fecha_nacimiento(texto, hoy=None):
    """Acepta año-mes-día; rechaza fechas futuras o de más de 120 años."""
    # 'hoy' se puede pasar para pruebas; si no, usa la fecha real.
    hoy = hoy or date.today()
    # Intenta leer año-mes-día.
    try:
        # Convierte el texto en fecha.
        f = date.fromisoformat((texto or "").strip())
    # Si no es una fecha real (31 de febrero, letras...)...
    except ValueError:
        # ...avisa el formato correcto.
        raise ErrorNegocio("La fecha de nacimiento no es válida. Use el formato año-mes-día "
                           "(ejemplo: 1985-03-27).")
    # No se puede nacer en el futuro.
    if f > hoy:
        # Mensaje de fecha futura.
        raise ErrorNegocio("La fecha de nacimiento es futura. Revise el año.")
    # Más de 120 años se considera error de tecleo.
    if (hoy - f).days > 120 * 365:
        # Mensaje de fecha demasiado antigua.
        raise ErrorNegocio("La fecha de nacimiento indica más de 120 años. Revise el año.")
    # Devuelve la fecha normalizada AAAA-MM-DD.
    return f.isoformat()


# ----------------------------------------------------------------- Pacientes
# Ejecuta una validación y, si falla, marca en qué casillero estuvo el problema.
def _en_campo(campo, fn, *args):
    """Ejecuta una validación y, si falla, anota qué casillero tiene el problema."""
    # Intenta la validación.
    try:
        # Si sale bien, devuelve el valor limpio.
        return fn(*args)
    # Si falla...
    except ErrorNegocio as e:
        # ...anota el casillero.
        e.campo = campo
        # ...y vuelve a lanzar el error.
        raise


# Valida todos los datos y crea al paciente. Devuelve su id.
def registrar_paciente(con, usuario, nombre, fecha_nacimiento, tipo_doc="ninguno",
                       documento=None, telefono=None, correo=None,
                       contacto_responsable=None, confirmar_homonimo=False, hoy=None):
    """Valida todo y guarda. Si ya existe alguien con el mismo nombre y fecha de
    nacimiento, pide confirmación en vez de crear un duplicado en silencio."""
    # Valida el nombre (campo: nombre).
    nombre_ok = _en_campo("nombre", limpiar_nombre, nombre)
    # Valida la fecha de nacimiento.
    fecha_ok = _en_campo("fecha_nacimiento", validar_fecha_nacimiento, fecha_nacimiento, hoy)
    # Valida el documento.
    tipo_ok, doc_ok = _en_campo("documento", validar_documento, tipo_doc, documento)
    # Valida el teléfono.
    tel_ok = _en_campo("telefono", validar_telefono, telefono)
    # Valida el correo.
    correo_ok = _en_campo("correo", validar_correo, correo)
    # Valida el teléfono del responsable (tutor o familiar).
    contacto_ok = _en_campo("contacto_responsable", validar_telefono, contacto_responsable)
    # Un paciente puede no tener ningún contacto.
    if tel_ok is None and correo_ok is None and contacto_ok is None:
        # No se bloquea: hay pacientes sin ningún medio de contacto. Se acepta y se avisa.
        # No se bloquea: se acepta.
        pass

    # Desde aquí todo se guarda junto o no se guarda.
    with transaccion(con):
        # Si trae documento, revisa que nadie más lo tenga.
        if doc_ok:
            # Busca otro paciente con el mismo tipo y número de documento.
            otro = con.execute("SELECT id, nombre FROM pacientes WHERE tipo_doc=? AND documento=?",
                               (tipo_ok, doc_ok)).fetchone()
            # Si existe...
            if otro:
                # ...se rechaza e indica quién es.
                raise ErrorNegocio(f"Ya existe un paciente con ese documento: {otro['nombre']}.",
                                   campo="documento")
        # Si la persona no confirmó que es alguien distinto...
        if not confirmar_homonimo:
            # ...busca a alguien con el mismo nombre (sin tildes) y la misma fecha de nacimiento.
            hom = con.execute("SELECT id FROM pacientes WHERE nombre_clave=? AND fecha_nacimiento=?",
                              (clave_nombre(nombre_ok), fecha_ok)).fetchone()
            # Si hay un posible duplicado...
            if hom:
                # ...se pide confirmación (código 'homonimo') en vez de duplicar en silencio.
                raise ErrorNegocio("Ya hay un paciente con el mismo nombre y fecha de nacimiento. "
                                   "Si es otra persona, confirme para crearlo igual.",
                                   codigo="homonimo")
        # Inserta al paciente con todos sus datos ya limpios.
        cur = con.execute(
            "INSERT INTO pacientes (nombre, nombre_clave, fecha_nacimiento, tipo_doc, documento,"
            " telefono, correo, contacto_responsable, creado_en) VALUES (?,?,?,?,?,?,?,?,?)",
            (nombre_ok, clave_nombre(nombre_ok), fecha_ok, tipo_ok, doc_ok, tel_ok, correo_ok,
             contacto_ok, datetime.now().isoformat(timespec="seconds")))
        # Anota en el historial quién lo creó.
        _auditar(con, usuario, "crear", "paciente", cur.lastrowid)
        # Devuelve el id del paciente nuevo.
        return cur.lastrowid


# ----------------------------------------------------------------- Citas
# Convierte '2026-11-23 09:30' en fecha y hora.
def _fecha_hora(texto):
    """Convierte '2026-11-23 09:30' en fecha y hora, o falla con un mensaje claro."""
    # Intenta leerla.
    try:
        # Convierte el texto.
        return datetime.fromisoformat((texto or "").strip())
    # Si no es válido...
    except ValueError:
        # ...mensaje claro; marca el campo fecha.
        raise ErrorNegocio("La fecha y hora no son válidas. Use el formato 2026-11-23 09:30.",
                           campo="fecha")


# Nombres de los días; 0 = lunes, igual que weekday() de Python.
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


# Comprueba que la cita cabe en el horario de la clínica.
def _validar_horario(con, clinica_id, ini, fin):
    """La cita debe caber dentro del horario de la clínica. Si la clínica no tiene
    horario cargado, no se restringe (ver DECISIONES.md)."""
    # Si la clínica no tiene ningún horario cargado, no se restringe nada.
    if not con.execute("SELECT 1 FROM horarios WHERE clinica_id=?", (clinica_id,)).fetchone():
        # Sale sin error.
        return
    # Nombre de la clínica para los mensajes.
    nombre = con.execute("SELECT nombre FROM clinicas WHERE id=?", (clinica_id,)).fetchone()["nombre"]
    # Horario de apertura y cierre para ese día de la semana.
    fila = con.execute("SELECT abre, cierra FROM horarios WHERE clinica_id=? AND dia_semana=?",
                       (clinica_id, ini.weekday())).fetchone()
    # Si no hay fila, la clínica está cerrada ese día.
    if not fila:
        # Nombre del día de la cita.
        dia = DIAS[ini.weekday()]
        # Pone el día en plural ('los lunes', 'los sábados').
        raise ErrorNegocio(f"{nombre} no atiende los {dia if dia.endswith('s') else dia + 's'}. "
                           "Elija otro día.", campo="fecha")
    # Error si cruza la medianoche, empieza antes de abrir o termina después de cerrar (se comparan como texto HH:MM).
    if (fin.date() != ini.date() or ini.strftime("%H:%M") < fila["abre"]
            or fin.strftime("%H:%M") > fila["cierra"]):
        # Dice el horario real de la clínica.
        raise ErrorNegocio(f"{nombre} atiende ese día de {fila['abre']} a {fila['cierra']}, y la cita "
                           "debe terminar antes del cierre. Elija otra hora.", campo="hora")


# Crea una cita validando todas las reglas. Devuelve el id.
def agendar_cita(con, usuario, paciente_id, medico_id, clinica_id, inicio,
                 duracion_min=30, ahora=None, clave=None):
    """`clave` (opcional) identifica el intento: si el navegador reintenta porque se cayó
    el internet, no se crea una segunda cita; se devuelve la primera."""
    # 'clave' identifica un intento para que un reintento no duplique la cita.
    if clave is not None:
        # La clave debe ser corta y solo letras, números o guiones.
        if not re.fullmatch(r"[A-Za-z0-9\-]{8,64}", str(clave)):
            # Mensaje si la clave es rara.
            raise ErrorNegocio("No se pudo identificar la solicitud. Recargue la página e intente de nuevo.")
        # Busca si ya existe una cita con esa clave.
        previa = con.execute("SELECT id FROM citas WHERE clave=?", (clave,)).fetchone()
        # Si existe, el intento ya se había procesado...
        if previa:
            # ...y se devuelve la misma cita en vez de crear otra.
            return previa["id"]
    # Hora actual (se puede pasar para pruebas).
    ahora = ahora or datetime.now()
    # Convierte el texto de inicio en fecha y hora.
    ini = _fecha_hora(inicio)
    # No se agenda en el pasado.
    if ini <= ahora:
        # Mensaje de fecha pasada.
        raise ErrorNegocio("Esa fecha y hora ya pasaron. Elija un horario futuro.", campo="fecha")
    # La duración debe estar entre 5 minutos y 4 horas.
    if not 5 <= duracion_min <= 240:
        # Mensaje de duración inválida.
        raise ErrorNegocio("La duración de la cita debe estar entre 5 minutos y 4 horas.")
    # Hora en que termina la cita.
    fin = ini + timedelta(minutes=duracion_min)
    # Inicio y fin como texto 'AAAA-MM-DDTHH:MM' para guardarlos.
    ini_s, fin_s = ini.isoformat(timespec="minutes"), fin.isoformat(timespec="minutes")

    # Atrapa el caso en que la base de datos rechace una doble reserva.
    try:
        # Todo lo siguiente es una sola operación protegida.
        with transaccion(con):
            # Revisa que existan el paciente, el médico y la clínica elegidos.
            for tabla, id_, que in (("pacientes", paciente_id, "paciente"),
                                    ("medicos", medico_id, "médico"),
                                    ("clinicas", clinica_id, "clínica")):
                # Un médico dado de baja no recibe citas.
                filtro = " AND activo=1" if tabla == "medicos" else ""   # médico dado de baja no recibe citas
                # Si no existe...
                if not con.execute(f"SELECT 1 FROM {tabla} WHERE id=?{filtro}", (id_,)).fetchone():
                    # ...avisa cuál de los tres falta.
                    raise ErrorNegocio(f"No se encontró el {que} seleccionado.")

            # Comprueba el horario de la clínica.
            _validar_horario(con, clinica_id, ini, fin)

            # El médico no puede estar en dos sitios a la vez (en ninguna clínica).
            # Busca citas activas del mismo médico (en cualquier clínica) que se solapen: empiezan antes de que termine la nueva y terminan después de que empiece.
            choque = con.execute(
                "SELECT cl.nombre FROM citas c JOIN clinicas cl ON cl.id = c.clinica_id"
                " WHERE c.medico_id=? AND c.estado='activa' AND c.inicio < ? AND c.fin > ?",
                (medico_id, fin_s, ini_s)).fetchone()
            # Si hay choque...
            if choque:
                # ...dice en qué clínica está ocupado.
                raise ErrorNegocio(f"El médico ya tiene una cita a esa hora en {choque['nombre']}. "
                                   "Elija otro horario.")
            # El paciente tampoco puede tener dos citas a la vez.
            # Lo mismo para el paciente: no puede tener dos citas a la vez.
            if con.execute(
                    "SELECT 1 FROM citas WHERE paciente_id=? AND estado='activa'"
                    " AND inicio < ? AND fin > ?", (paciente_id, fin_s, ini_s)).fetchone():
                # Mensaje de paciente ocupado.
                raise ErrorNegocio("Este paciente ya tiene otra cita a esa hora.")

            # Si todo está bien, guarda la cita.
            cur = con.execute(
                "INSERT INTO citas (paciente_id, medico_id, clinica_id, inicio, fin, clave, creado_por, creado_en)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (paciente_id, medico_id, clinica_id, ini_s, fin_s, clave, usuario,
                 datetime.now().isoformat(timespec="seconds")))
            # Anota quién agendó.
            _auditar(con, usuario, "agendar", "cita", cur.lastrowid, f"{ini_s} medico={medico_id}")
            # Devuelve el id de la cita.
            return cur.lastrowid
    # Dos personas intentaron la misma hora al mismo tiempo: la base de datos dejó pasar solo a una.
    except sqlite3.IntegrityError:
        # Red de seguridad: la base de datos rechazó una doble reserva (o un reintento simultáneo).
        # Si era un reintento...
        if clave:
            # ...busca la cita ya creada...
            previa = con.execute("SELECT id FROM citas WHERE clave=?", (clave,)).fetchone()
            # ...y si existe...
            if previa:
                # ...devuelve esa.
                return previa["id"]
        # Si no, la hora la ganó otra persona.
        raise ErrorNegocio("Esa hora acaba de ser tomada por otra persona. Elija otro horario.")


# Cancela una cita. Cancelar dos veces no da error.
def cancelar_cita(con, usuario, cita_id):
    """Idempotente: cancelar dos veces no da error ni ensucia la auditoría."""
    # Operación protegida.
    with transaccion(con):
        # Lee el estado actual.
        cita = con.execute("SELECT estado FROM citas WHERE id=?", (cita_id,)).fetchone()
        # Si no existe...
        if not cita:
            # ...error.
            raise ErrorNegocio("No se encontró esa cita.")
        # Si ya estaba cancelada...
        if cita["estado"] == "cancelada":
            # ...responde sin error y sin ensuciar la auditoría.
            return "Esa cita ya estaba cancelada."
        # Cambia el estado: la hora queda libre porque el índice único solo cuenta las activas.
        con.execute("UPDATE citas SET estado='cancelada' WHERE id=?", (cita_id,))
        # Anota quién canceló.
        _auditar(con, usuario, "cancelar", "cita", cita_id)
        # Mensaje de éxito.
        return "Cita cancelada. La hora quedó libre."


# Reactiva una cita cancelada, solo si su hora sigue libre.
def deshacer_cancelacion(con, usuario, cita_id, ahora=None):
    """Botón 'Deshacer': reactiva la cita solo si su hora sigue libre."""
    # Hora actual.
    ahora = ahora or datetime.now()
    # Atrapa choques de la base de datos.
    try:
        # Operación protegida.
        with transaccion(con):
            # Lee la cita.
            c = con.execute("SELECT * FROM citas WHERE id=?", (cita_id,)).fetchone()
            # Si no existe...
            if not c:
                # ...error.
                raise ErrorNegocio("No se encontró esa cita.")
            # Si ya está activa no hay nada que hacer.
            if c["estado"] == "activa":
                # Respuesta sin error.
                return "Esa cita ya estaba activa."
            # No se reactiva una cita que ya pasó.
            if datetime.fromisoformat(c["inicio"]) <= ahora:
                # Mensaje.
                raise ErrorNegocio("Esa cita ya pasó, no se puede reactivar.")
            # Revisa que nadie haya ocupado esa hora mientras tanto.
            if con.execute("SELECT 1 FROM citas WHERE medico_id=? AND estado='activa'"
                           " AND inicio < ? AND fin > ?", (c["medico_id"], c["fin"], c["inicio"])).fetchone():
                # Mensaje de hora ocupada.
                raise ErrorNegocio("No se puede deshacer: esa hora ya fue ocupada por otra cita.")
            # Reactiva la cita.
            con.execute("UPDATE citas SET estado='activa' WHERE id=?", (cita_id,))
            # Anota quién la reactivó.
            _auditar(con, usuario, "reactivar", "cita", cita_id)
            # Mensaje de éxito.
            return "Cita reactivada."
    # Si la base de datos rechaza por hora duplicada...
    except sqlite3.IntegrityError:
        # ...mismo mensaje.
        raise ErrorNegocio("No se puede deshacer: esa hora ya fue ocupada por otra cita.")


# ----------------------------------------------------------------- Usuarios y permisos
# Roles que existen en el sistema.
ROLES = ("recepcion", "doctora")
# Qué puede hacer cada rol. La recepción no ve contactos ni historial.
PERMISOS = {
    "recepcion": {"agendar", "cancelar", "registrar_paciente"},
    "doctora": {"agendar", "cancelar", "registrar_paciente", "ver_contacto", "ver_auditoria"},
}
# Cuántas contraseñas incorrectas seguidas se permiten antes de bloquear.
INTENTOS_MAX = 5      # PIN equivocado 5 veces seguidas -> bloqueo temporal
# Minutos que dura el bloqueo.
BLOQUEO_MIN = 5


# Responde si un rol tiene un permiso.
def puede(rol, permiso):
    """Dice si un rol tiene un permiso (ver PERMISOS)."""
    # Si el rol no existe, no tiene ningún permiso.
    return permiso in PERMISOS.get(rol, ())


# Huella irreversible del PIN.
def _huella(pin, sal):
    """Huella irreversible del PIN (scrypt) con una sal."""
    # scrypt es lento a propósito para dificultar adivinar PIN por fuerza bruta; la sal hace distinta cada huella.
    return hashlib.scrypt(pin.encode(), salt=sal, n=2 ** 14, r=8, p=1).hex()


# Prepara lo que se guarda en la base.
def _hash_pin(pin):
    """Crea lo que se guarda: sal aleatoria + huella. El PIN en sí nunca se guarda."""
    # Sal aleatoria de 16 bytes: dos usuarios con el mismo PIN tienen huellas distintas.
    sal = os.urandom(16)
    # Se guarda 'sal$huella'. El PIN nunca se guarda.
    return sal.hex() + "$" + _huella(pin, sal)


# Comprueba un PIN contra lo guardado.
def _pin_correcto(pin, guardado):
    """Compara un PIN con lo guardado en tiempo constante (evita adivinarlo midiendo tiempos)."""
    # Separa la sal de la huella.
    sal_hex, huella = guardado.split("$")
    # Compara en tiempo constante, para que no se pueda deducir el PIN midiendo cuánto tarda.
    return hmac.compare_digest(_huella(pin, bytes.fromhex(sal_hex)), huella)


# El PIN debe ser de 4 a 12 números.
def _validar_pin(pin):
    """El PIN debe tener entre 4 y 12 números."""
    # Verifica el formato.
    if not re.fullmatch(r"[0-9]{4,12}", pin or ""):
        # Mensaje.
        raise ErrorNegocio("El PIN debe tener entre 4 y 12 números.", campo="pin")
    # PIN válido.
    return pin


# Crea una persona que puede entrar al sistema.
def crear_usuario(con, nombre, rol, pin):
    """Crea una persona que puede entrar al sistema, con su rol y su PIN."""
    # Limpia espacios.
    nombre = normalizar_espacios(nombre)
    # Nombre de 3 a 50 caracteres.
    if not 3 <= len(nombre) <= 50:
        # Mensaje.
        raise ErrorNegocio("El nombre de usuario debe tener entre 3 y 50 caracteres.")
    # Solo roles conocidos.
    if rol not in ROLES:
        # Lista los roles posibles.
        raise ErrorNegocio("Rol no válido. Use: " + ", ".join(ROLES) + ".")
    # Intenta guardar.
    try:
        # Guarda nombre, rol y la huella del PIN (validado primero).
        cur = con.execute("INSERT INTO usuarios (nombre, rol, pin_hash) VALUES (?,?,?)",
                          (nombre, rol, _hash_pin(_validar_pin(pin))))
    # El nombre es único: si ya existe...
    except sqlite3.IntegrityError:
        # ...avisa.
        raise ErrorNegocio("Ya existe un usuario con ese nombre.")
    # Devuelve el id del usuario.
    return cur.lastrowid


# Cambia el PIN y quita cualquier bloqueo.
def cambiar_pin(con, nombre, pin_nuevo):
    """Cambia el PIN de un usuario y le quita cualquier bloqueo."""
    # Guarda la nueva huella y pone los intentos en cero.
    cur = con.execute("UPDATE usuarios SET pin_hash=?, intentos=0, bloqueado_hasta=NULL WHERE nombre=?",
                      (_hash_pin(_validar_pin(pin_nuevo)), normalizar_espacios(nombre)))
    # Si no se modificó ninguna fila, el usuario no existe.
    if cur.rowcount == 0:
        # Mensaje.
        raise ErrorNegocio("No existe ese usuario.")


# NUEVO: encuentra al usuario por lo que escribió en la pantalla de ingreso.
def _buscar_usuario(con, escrito):
    """Encuentra al usuario por lo que escribió: nombre completo o solo el primer nombre ('rosa'),
    sin importar mayúsculas ni tildes. Si el atajo coincide con más de uno, no adivina."""
    # Normaliza lo escrito (sin tildes, mayúsculas ni espacios de más).
    clave = clave_nombre(str(escrito or ""))
    # Si no escribió nada, no hay a quién buscar.
    if not clave:
        # Sin resultado.
        return None
    # Trae los usuarios activos (son pocos, se compara en Python).
    activos = con.execute("SELECT * FROM usuarios WHERE activo=1").fetchall()
    # Primero busca coincidencia con el nombre completo.
    exactos = [u for u in activos if clave_nombre(u["nombre"]) == clave]
    # Si la hay, esa gana (así 'Rosa' no se confunde con 'Rosa María').
    if exactos:
        # Devuelve ese usuario.
        return exactos[0]
    # Si no, prueba con el primer nombre, ignorando lo que esté entre paréntesis: 'Rosa (recepción)' -> 'rosa'.
    atajo = [u for u in activos if clave_nombre(re.sub(r"\(.*?\)", "", u["nombre"])).split(" ")[:1] == [clave]]
    # Acepta el atajo solo si coincide exactamente con una persona; si hay varias, no adivina.
    return atajo[0] if len(atajo) == 1 else None


# Verifica usuario y contraseña. Tras 5 errores seguidos bloquea 5 minutos.
def iniciar_sesion(con, nombre, pin, ahora=None):
    """Devuelve {'nombre', 'rol'} o falla con un mensaje que no revela si el usuario existe.
    Tras INTENTOS_MAX errores seguidos, bloquea BLOQUEO_MIN minutos."""
    # Hora actual (se puede pasar para pruebas).
    ahora = ahora or datetime.now()
    # Mismo error para usuario inexistente y contraseña mala: no se revela qué usuarios existen.
    mal = ErrorNegocio("Usuario o contraseña incorrectos.", campo="pin")
    # Cambio: antes comparaba el nombre exacto; ahora busca de forma tolerante.
    u = _buscar_usuario(con, nombre)
    # Si no existe...
    if not u:
        # ...error genérico.
        raise mal
    # Si está bloqueado y el bloqueo no ha vencido...
    if u["bloqueado_hasta"] and datetime.fromisoformat(u["bloqueado_hasta"]) > ahora:
        # ...no deja entrar ni con la contraseña correcta.
        raise ErrorNegocio(f"Demasiados intentos. Espere {BLOQUEO_MIN} minutos e intente de nuevo.", campo="pin")
    # Convierte a texto (None = vacío).
    pin = str(pin or "")
    # Límite de largo para no procesar textos enormes; si es correcto...
    if len(pin) <= 64 and _pin_correcto(pin, u["pin_hash"]):
        # ...se ponen los intentos en cero.
        con.execute("UPDATE usuarios SET intentos=0, bloqueado_hasta=NULL WHERE id=?", (u["id"],))
        # Anota el ingreso en el historial.
        _auditar(con, u["nombre"], "ingresar", "usuario", u["id"])
        # Devuelve nombre y rol para crear la sesión.
        return {"nombre": u["nombre"], "rol": u["rol"]}
    # Contraseña incorrecta: suma un intento fallido.
    intentos = u["intentos"] + 1
    # Si llegó al máximo, calcula hasta cuándo queda bloqueado.
    bloqueo = ((ahora + timedelta(minutes=BLOQUEO_MIN)).isoformat(timespec="seconds")
               if intentos >= INTENTOS_MAX else None)
    # Guarda el nuevo conteo (en cero si se activó el bloqueo) y la hora de bloqueo.
    con.execute("UPDATE usuarios SET intentos=?, bloqueado_hasta=? WHERE id=?",
                (0 if bloqueo else intentos, bloqueo, u["id"]))
    # Anota el intento fallido para la doctora.
    _auditar(con, u["nombre"], "ingreso_fallido", "usuario", u["id"])
    # Error genérico de usuario o contraseña.
    raise mal


# Devuelve el historial de cambios.
def listar_auditoria(con, limite=200):
    """Quién hizo qué y cuándo, lo más reciente primero."""
    # Los 200 eventos más recientes primero, como diccionarios.
    return [dict(r) for r in con.execute(
        "SELECT cuando, usuario, accion, entidad, entidad_id, detalle FROM auditoria"
        " ORDER BY id DESC LIMIT ?", (limite,))]