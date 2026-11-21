"""Importa pacientes desde un CSV "sucio" (como lo entregaría una clínica real).

Uso (primero SIEMPRE en prueba, no guarda nada):
    python importar.py pacientes.csv
Cuando el informe se vea bien:
    python importar.py pacientes.csv --aplicar

Reglas (ver DECISIONES.md):
  * Nunca se detiene por una fila mala: cada fila se importa, se importa con avisos o se rechaza.
  * Un dato opcional malo (teléfono, correo, documento) NO impide guardar al paciente:
    se deja en blanco y se anota en el informe con el valor original, para que una persona lo corrija.
  * Nombre o fecha de nacimiento inválidos -> se rechaza la fila (no se puede registrar sin ellos).
  * Posibles duplicados (mismo nombre y fecha, o mismo documento) -> NO se mezclan solos: se listan para revisar.
  * Se arreglan solo los errores seguros: el "0" inicial que Excel borra en cédulas y celulares,
    pero SOLO si la cédula resulta válida con su dígito verificador.
El informe se guarda junto al archivo como importacion_informe.csv (contiene nombres: no subirlo a GitHub).
"""
# csv: lee y escribe archivos CSV.
import csv
# io: permite tratar un texto como si fuera un archivo.
import io
# os: lee la variable de entorno MSLS_DB.
import os
# re: expresiones regulares para fechas, documentos y teléfonos.
import re
# sqlite3: base de datos (aquí, para la copia en memoria del modo prueba).
import sqlite3
# sys: argumentos de la línea de comandos.
import sys
# Tipos de fecha.
from datetime import date, datetime
# Rutas de archivos.
from pathlib import Path

# Reglas de negocio: se reutilizan las MISMAS validaciones que la pantalla.
import nucleo
# Error pensado para mostrarse a la persona.
from nucleo import ErrorNegocio

# Nombres con los que distintas clínicas llaman a cada columna ('cédula', 'ci', 'celular'...). Así el archivo no necesita tener títulos exactos.
ALIAS = {
    "nombre": {"nombre", "nombres", "paciente", "nombre completo", "nombres y apellidos", "apellidos y nombres"},
    "fecha_nacimiento": {"fecha_nacimiento", "fecha nacimiento", "nacimiento", "fecha de nacimiento", "fnac", "fecha nac"},
    "documento": {"documento", "cedula", "cédula", "identificacion", "identificación", "ci", "cedula/pasaporte", "id"},
    "telefono": {"telefono", "teléfono", "celular", "telf", "movil", "móvil", "contacto"},
    "correo": {"correo", "email", "e-mail", "mail", "correo electronico", "correo electrónico"},
}


# Normaliza un título de columna para compararlo (sin tildes ni mayúsculas).
def _clave(texto):
    """Normaliza un título de columna para compararlo (sin tildes ni mayúsculas)."""
    # Reutiliza la función del núcleo y cambia '_' por espacio.
    return nucleo.clave_nombre(texto).replace("_", " ")


# Lee el CSV y devuelve (columnas encontradas, filas de datos).
def leer_filas(contenido_bytes):
    """Prueba UTF-8 (con o sin BOM) y luego Latin-1; adivina el separador (, ; tab)."""
    # Prueba primero UTF-8 (con o sin BOM de Excel) y luego Latin-1.
    for codificacion in ("utf-8-sig", "latin-1"):
        # Intenta decodificar.
        try:
            # Convierte los bytes en texto.
            texto = contenido_bytes.decode(codificacion)
            # Si funcionó, deja de probar.
            break
        # Si no era esa codificación...
        except UnicodeDecodeError:
            # ...prueba la siguiente.
            continue
    # Primeros 4096 caracteres para adivinar el separador.
    muestra = texto[:4096]
    # Intenta adivinar.
    try:
        # Detecta si se separa con coma, punto y coma, tabulador o barra.
        dialecto = csv.Sniffer().sniff(muestra, delimiters=",;\t|")
    # Si no puede...
    except csv.Error:
        # ...asume coma (formato Excel).
        dialecto = csv.excel
    # Lector de CSV sobre el texto.
    lector = csv.reader(io.StringIO(texto), dialecto)
    # Descarta las filas completamente vacías.
    filas = [f for f in lector if any(c.strip() for c in f)]
    # Si no queda nada...
    if not filas:
        # ...avisa.
        raise ErrorNegocio("El archivo está vacío.")
    # Mapa campo -> posición de la columna.
    columnas = {}
    # Recorre los títulos de la primera fila.
    for i, titulo in enumerate(filas[0]):
        # Para cada campo conocido...
        for campo, alias in ALIAS.items():
            # ...si el título coincide con alguno de sus alias y aún no se asignó...
            if _clave(titulo) in {_clave(a) for a in alias} and campo not in columnas:
                # ...guarda en qué columna está.
                columnas[campo] = i
    # Campos obligatorios que no se encontraron (nombre y fecha de nacimiento).
    faltan = [c for c in ("nombre", "fecha_nacimiento") if c not in columnas]
    # Si falta alguno...
    if faltan:
        # ...avisa cuáles, para que revisen los títulos.
        raise ErrorNegocio("No encontré la columna de: " + ", ".join(faltan) +
                           ". Revise los títulos de la primera fila.")
    # Devuelve las columnas y las filas sin la de títulos.
    return columnas, filas[1:]


# Acepta 1985-03-27, 27/03/1985 y 27-03-1985 (día primero, como en Ecuador). No adivina años de 2 dígitos.
def parsear_fecha(texto):
    """Acepta 1985-03-27, 27/03/1985 y 27-03-1985 (día primero, como en Ecuador). Años de 2 dígitos: no se adivinan."""
    # Quita espacios.
    t = (texto or "").strip()
    # ¿Viene como AAAA-MM-DD?
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    # Si sí...
    if m:
        # ...separa año, mes y día.
        a, mes, d = map(int, m.groups())
    # Si no...
    else:
        # ...prueba DD/MM/AAAA, con / - o . como separador.
        m = re.fullmatch(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})", t)
        # Si tampoco...
        if not m:
            # ...no se entiende la fecha.
            raise ErrorNegocio(f"fecha no reconocida: «{t}»")
        # Separa día, mes y año.
        d, mes, a = map(int, m.groups())
    # Intenta construir la fecha.
    try:
        # Devuelve AAAA-MM-DD.
        return date(a, mes, d).isoformat()
    # Si no existe (31/02)...
    except ValueError:
        # ...avisa.
        raise ErrorNegocio(f"fecha imposible: «{t}»")


# Devuelve (tipo, documento). Si algo no cuadra: ('ninguno', None) y un aviso con el valor original.
def _documento(crudo, avisos):
    """Devuelve (tipo, documento). Si algo no cuadra: ('ninguno', None) y un aviso con el valor original."""
    # Quita espacios, puntos y guiones.
    d = re.sub(r"[\s.\-]", "", crudo or "")
    # Si quedó vacío...
    if not d:
        # ...no tiene documento.
        return "ninguno", None
    # Si son solo números, se trata como cédula.
    if re.fullmatch(r"[0-9]+", d):
        # Excel borra el 0 inicial: si tiene 9 dígitos, también se prueba con 0 delante.
        candidatos = [d] + (["0" + d] if len(d) == 9 else [])   # Excel borra el 0 inicial
        # Prueba cada candidato.
        for c in candidatos:
            # Intenta validarlo.
            try:
                # Con el dígito verificador: solo se 'arregla' si cuadra.
                nucleo.validar_cedula(c)
                # Si hubo que agregar el 0...
                if c != d:
                    # ...se anota en el informe.
                    avisos.append(f"se agregó el 0 inicial a la cédula ({crudo} -> {c})")
                # Cédula válida.
                return "cedula", c
            # Si no es válida...
            except ErrorNegocio:
                # ...prueba el siguiente candidato.
                pass
        # Ninguno sirvió: no se inventa nada; se importa sin documento y se avisa.
        avisos.append(f"cédula no válida «{crudo}»: se importó sin documento")
        # Sin documento.
        return "ninguno", None
    # Si trae letras, se intenta como pasaporte.
    try:
        # Pasaporte válido.
        return nucleo.validar_documento("pasaporte", d)
    # Si no sirve...
    except ErrorNegocio:
        # ...se importa sin documento y se avisa.
        avisos.append(f"documento no reconocido «{crudo}»: se importó sin documento")
        # Sin documento.
        return "ninguno", None


# Limpia un teléfono; si no es válido lo deja en blanco y avisa (no frena al paciente).
def _telefono(crudo, avisos):
    """Limpia un teléfono; si no es válido lo deja en blanco y anota un aviso (no frena al paciente)."""
    # Quita espacios, guiones, paréntesis y puntos.
    t = re.sub(r"[\s\-().]", "", crudo or "")
    # Celular de 9 dígitos que empieza con 9: Excel le borró el 0.
    if re.fullmatch(r"9\d{8}", t):   # Excel borra el 0 inicial del celular
        # Se lo devuelve.
        t = "0" + t
    # Intenta validar.
    try:
        # Teléfono correcto.
        return nucleo.validar_telefono(t)
    # Si no...
    except ErrorNegocio:
        # ...se deja en blanco y se avisa.
        avisos.append(f"teléfono no válido «{crudo}»: se dejó en blanco")
        # Sin teléfono.
        return None


# Igual que _telefono, pero para el correo.
def _correo(crudo, avisos):
    """Igual que _telefono, pero para el correo."""
    # Intenta validar.
    try:
        # Correo correcto.
        return nucleo.validar_correo(crudo)
    # Si no...
    except ErrorNegocio:
        # ...se deja en blanco y se avisa.
        avisos.append(f"correo no válido «{crudo}»: se dejó en blanco")
        # Sin correo.
        return None


# Importa fila por fila. Devuelve (resumen, informe). Sin 'aplicar' trabaja en una copia en memoria y no toca la base real.
def importar_pacientes(con, columnas, filas, aplicar=False, hoy=None):
    """Devuelve (resumen, informe). Sin `aplicar`, trabaja sobre una copia en memoria y no toca la base real."""
    # Por defecto, la base real.
    destino = con
    # En modo prueba...
    if not aplicar:
        # ...crea una base temporal...
        destino = sqlite3.connect(":memory:", isolation_level=None)
        # ...con acceso por nombre de columna...
        destino.row_factory = sqlite3.Row
        # ...y le copia la base real para que las comprobaciones (duplicados) sean fieles.
        con.backup(destino)
    # Contadores del resultado.
    resumen = {"importados": 0, "con_avisos": 0, "rechazados": 0, "revisar": 0}
    # Lista de lo que merece revisión.
    informe = []
    # Atajo: lee una columna de una fila (vacío si no existe).
    get = lambda f, campo: (f[columnas[campo]] if campo in columnas and columnas[campo] < len(f) else "").strip()
    # Recorre las filas; empiezan en 2 porque la 1 son los títulos.
    for n, f in enumerate(filas, start=2):   # la fila 1 son los títulos
        # Nombre tal como viene.
        nombre = get(f, "nombre")
        # Avisos de esta fila.
        avisos = []
        # Una fila mala nunca detiene a las demás.
        try:
            # Valida el nombre primero (si falla, la fila se rechaza).
            nucleo.limpiar_nombre(nombre)
            # Convierte la fecha de nacimiento.
            nac = parsear_fecha(get(f, "fecha_nacimiento"))
            # Limpia el documento (opcional).
            tipo, doc = _documento(get(f, "documento"), avisos)
            # Limpia teléfono y correo (opcionales).
            tel, cor = _telefono(get(f, "telefono"), avisos), _correo(get(f, "correo"), avisos)
            # Guarda con las mismas reglas que la pantalla; el usuario queda como 'importacion'.
            nucleo.registrar_paciente(destino, "importacion", nombre, nac, tipo, doc, tel, cor, hoy=hoy)
        # Si la fila no se puede guardar...
        except ErrorNegocio as e:
            # ...se clasifica: posible duplicado (a revisar por una persona) o rechazada.
            clasif = "revisar" if (e.codigo == "homonimo" or "Ya existe un paciente con ese documento" in str(e)) else "rechazados"
            # Cuenta.
            resumen[clasif] += 1
            # Anota en el informe fila, resultado, motivo y nombre.
            informe.append((n, "posible duplicado" if clasif == "revisar" else "rechazada", str(e), nombre))
            # Pasa a la siguiente fila.
            continue
        # Si se guardó pero con avisos...
        if avisos:
            # ...cuenta como 'con avisos'...
            resumen["con_avisos"] += 1
            # ...y anota qué se dejó en blanco.
            informe.append((n, "importada con avisos", "; ".join(avisos), nombre))
        # Si todo estaba bien...
        else:
            # ...cuenta como importada limpia.
            resumen["importados"] += 1
    # Si era prueba...
    if not aplicar:
        # ...descarta la copia temporal.
        destino.close()
    # Devuelve los resultados.
    return resumen, informe


# Línea de comandos.
def main(argv):
    """Línea de comandos: lee el CSV, importa (o simula) y escribe el informe."""
    # Sin archivo...
    if not argv:
        # ...muestra la ayuda.
        print(__doc__)
        # Termina con error.
        return 1
    # Archivo a importar y si se pidió --aplicar.
    ruta, aplicar = Path(argv[0]), "--aplicar" in argv
    # Lee el CSV.
    columnas, filas = leer_filas(ruta.read_bytes())
    # Base de datos: variable MSLS_DB o MSLS.db en la carpeta principal.
    destino = os.environ.get("MSLS_DB") or Path(__file__).resolve().parent.parent / "MSLS.db"
    # Abre la base.
    con = nucleo.conectar(str(destino))
    # Importa.
    try:
        # Ejecuta (en prueba o aplicando).
        resumen, informe = importar_pacientes(con, columnas, filas, aplicar)
    # Pase lo que pase...
    finally:
        # ...cierra la base.
        con.close()
    # El informe se guarda junto al archivo: importacion_informe.csv (contiene nombres: no subirlo a GitHub).
    salida = ruta.with_name("importacion_informe.csv")
    # Abre el informe (utf-8-sig para que Excel muestre bien las tildes).
    with open(salida, "w", newline="", encoding="utf-8-sig") as f:
        # Escritor de CSV.
        w = csv.writer(f)
        # Títulos.
        w.writerow(["fila", "resultado", "detalle", "nombre"])
        # Todas las filas del informe.
        w.writerows(informe)
    # Muestra en consola el resumen.
    print(("APLICADO" if aplicar else " PRUEBA (no se gurado) "), resumen)
    # Dice dónde quedó el informe.
    print("Informe:", salida)
    # Terminó bien.
    return 0


# ATENCIÓN: aquí hay un error que ya existía: dice " main" en vez de "__main__"; por eso el programa no arranca desde la terminal.
if __name__ == " main":
    # Llama a main con los argumentos y sale con su código.
    sys.exit(main(sys.argv[1:]))