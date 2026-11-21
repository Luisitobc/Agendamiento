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
import csv
import io
import os
import re
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

import nucleo
from nucleo import ErrorNegocio

ALIAS = {
    "nombre": {"nombre", "nombres", "paciente", "nombre completo", "nombres y apellidos", "apellidos y nombres"},
    "fecha_nacimiento": {"fecha_nacimiento", "fecha nacimiento", "nacimiento", "fecha de nacimiento", "fnac", "fecha nac"},
    "documento": {"documento", "cedula", "cédula", "identificacion", "identificación", "ci", "cedula/pasaporte", "id"},
    "telefono": {"telefono", "teléfono", "celular", "telf", "movil", "móvil", "contacto"},
    "correo": {"correo", "email", "e-mail", "mail", "correo electronico", "correo electrónico"},
}


def _clave(texto):
    """Normaliza un título de columna para compararlo (sin tildes ni mayúsculas)."""
    return nucleo.clave_nombre(texto).replace("_", " ")


def leer_filas(contenido_bytes):
    """Prueba UTF-8 (con o sin BOM) y luego Latin-1; adivina el separador (, ; tab)."""
    for codificacion in ("utf-8-sig", "latin-1"):
        try:
            texto = contenido_bytes.decode(codificacion)
            break
        except UnicodeDecodeError:
            continue
    muestra = texto[:4096]
    try:
        dialecto = csv.Sniffer().sniff(muestra, delimiters=",;\t|")
    except csv.Error:
        dialecto = csv.excel
    lector = csv.reader(io.StringIO(texto), dialecto)
    filas = [f for f in lector if any(c.strip() for c in f)]
    if not filas:
        raise ErrorNegocio("El archivo está vacío.")
    columnas = {}
    for i, titulo in enumerate(filas[0]):
        for campo, alias in ALIAS.items():
            if _clave(titulo) in {_clave(a) for a in alias} and campo not in columnas:
                columnas[campo] = i
    faltan = [c for c in ("nombre", "fecha_nacimiento") if c not in columnas]
    if faltan:
        raise ErrorNegocio("No encontré la columna de: " + ", ".join(faltan) +
                           ". Revise los títulos de la primera fila.")
    return columnas, filas[1:]


def parsear_fecha(texto):
    """Acepta 1985-03-27, 27/03/1985 y 27-03-1985 (día primero, como en Ecuador). Años de 2 dígitos: no se adivinan."""
    t = (texto or "").strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        a, mes, d = map(int, m.groups())
    else:
        m = re.fullmatch(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})", t)
        if not m:
            raise ErrorNegocio(f"fecha no reconocida: «{t}»")
        d, mes, a = map(int, m.groups())
    try:
        return date(a, mes, d).isoformat()
    except ValueError:
        raise ErrorNegocio(f"fecha imposible: «{t}»")


def _documento(crudo, avisos):
    """Devuelve (tipo, documento). Si algo no cuadra: ('ninguno', None) y un aviso con el valor original."""
    d = re.sub(r"[\s.\-]", "", crudo or "")
    if not d:
        return "ninguno", None
    if re.fullmatch(r"[0-9]+", d):
        candidatos = [d] + (["0" + d] if len(d) == 9 else [])   # Excel borra el 0 inicial
        for c in candidatos:
            try:
                nucleo.validar_cedula(c)
                if c != d:
                    avisos.append(f"se agregó el 0 inicial a la cédula ({crudo} -> {c})")
                return "cedula", c
            except ErrorNegocio:
                pass
        avisos.append(f"cédula no válida «{crudo}»: se importó sin documento")
        return "ninguno", None
    try:
        return nucleo.validar_documento("pasaporte", d)
    except ErrorNegocio:
        avisos.append(f"documento no reconocido «{crudo}»: se importó sin documento")
        return "ninguno", None


def _telefono(crudo, avisos):
    """Limpia un teléfono; si no es válido lo deja en blanco y anota un aviso (no frena al paciente)."""
    t = re.sub(r"[\s\-().]", "", crudo or "")
    if re.fullmatch(r"9\d{8}", t):   # Excel borra el 0 inicial del celular
        t = "0" + t
    try:
        return nucleo.validar_telefono(t)
    except ErrorNegocio:
        avisos.append(f"teléfono no válido «{crudo}»: se dejó en blanco")
        return None


def _correo(crudo, avisos):
    """Igual que _telefono, pero para el correo."""
    try:
        return nucleo.validar_correo(crudo)
    except ErrorNegocio:
        avisos.append(f"correo no válido «{crudo}»: se dejó en blanco")
        return None


def importar_pacientes(con, columnas, filas, aplicar=False, hoy=None):
    """Devuelve (resumen, informe). Sin `aplicar`, trabaja sobre una copia en memoria y no toca la base real."""
    destino = con
    if not aplicar:
        destino = sqlite3.connect(":memory:", isolation_level=None)
        destino.row_factory = sqlite3.Row
        con.backup(destino)
    resumen = {"importados": 0, "con_avisos": 0, "rechazados": 0, "revisar": 0}
    informe = []
    get = lambda f, campo: (f[columnas[campo]] if campo in columnas and columnas[campo] < len(f) else "").strip()
    for n, f in enumerate(filas, start=2):   # la fila 1 son los títulos
        nombre = get(f, "nombre")
        avisos = []
        try:
            nucleo.limpiar_nombre(nombre)
            nac = parsear_fecha(get(f, "fecha_nacimiento"))
            tipo, doc = _documento(get(f, "documento"), avisos)
            tel, cor = _telefono(get(f, "telefono"), avisos), _correo(get(f, "correo"), avisos)
            nucleo.registrar_paciente(destino, "importacion", nombre, nac, tipo, doc, tel, cor, hoy=hoy)
        except ErrorNegocio as e:
            clasif = "revisar" if (e.codigo == "homonimo" or "Ya existe un paciente con ese documento" in str(e)) else "rechazados"
            resumen[clasif] += 1
            informe.append((n, "posible duplicado" if clasif == "revisar" else "rechazada", str(e), nombre))
            continue
        if avisos:
            resumen["con_avisos"] += 1
            informe.append((n, "importada con avisos", "; ".join(avisos), nombre))
        else:
            resumen["importados"] += 1
    if not aplicar:
        destino.close()
    return resumen, informe


def main(argv):
    """Línea de comandos: lee el CSV, importa (o simula) y escribe el informe."""
    if not argv:
        print(__doc__)
        return 1
    ruta, aplicar = Path(argv[0]), "--aplicar" in argv
    columnas, filas = leer_filas(ruta.read_bytes())
    destino = os.environ.get("MSLS_DB") or Path(__file__).resolve().parent.parent / "MSLS.db"
    con = nucleo.conectar(str(destino))
    try:
        resumen, informe = importar_pacientes(con, columnas, filas, aplicar)
    finally:
        con.close()
    salida = ruta.with_name("importacion_informe.csv")
    with open(salida, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["fila", "resultado", "detalle", "nombre"])
        w.writerows(informe)
    print(("APLICADO" if aplicar else " PRUEBA (no se gurado) "), resumen)
    print("Informe:", salida)
    return 0


if __name__ == " main":
    sys.exit(main(sys.argv[1:]))
