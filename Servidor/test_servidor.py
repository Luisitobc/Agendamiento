"""Prueba del servidor real (base temporal). Ejecutar: python -m unittest -v"""
import http.cookiejar
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import servidor


class Cliente:
    """Un navegador de mentira: guarda la cookie de sesión."""
    def __init__(self, base):
        self.base = base
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def pedir(self, ruta, cuerpo=None, tipo="application/json"):
        datos = None if cuerpo is None else (cuerpo if isinstance(cuerpo, bytes) else json.dumps(cuerpo).encode())
        req = urllib.request.Request(self.base + ruta, method="POST" if cuerpo is not None else "GET",
                                     data=datos, headers={"Content-Type": tipo} if cuerpo is not None else {})
        try:
            with self.opener.open(req) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def entrar(self, nombre, pin="1234"):
        return self.pedir("/api/login", {"nombre": nombre, "pin": pin})


class TestServidor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        servidor.RUTA_DB = str(Path(cls.tmp.name) / "prueba.db")
        servidor.preparar_base()
        cls.http = ThreadingHTTPServer(("127.0.0.1", 0), servidor.Manejador)
        cls.base = f"http://127.0.0.1:{cls.http.server_address[1]}"
        threading.Thread(target=cls.http.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.tmp.cleanup()

    def setUp(self):
        self.rosa, self.dra = Cliente(self.base), Cliente(self.base)
        self.assertEqual(self.rosa.entrar("Rosa (recepción)")[0], 200)
        self.assertEqual(self.dra.entrar("Dra. Paredes")[0], 200)

    # ---- páginas
    def test_pagina_principal_y_fondo(self):
        for ruta in ("/", "/fondo_suave.svg", "/ilustracion_clinica.svg"):
            with urllib.request.urlopen(self.base + ruta) as r:
                self.assertEqual(r.status, 200)

    def test_no_sirve_archivos_arbitrarios(self):
        for ruta in ("/servidor.py", "/../nucleo.py", "/MSLS.db"):
            self.assertEqual(Cliente(self.base).pedir(ruta)[0], 404)

    # ---- sesión
    def test_sin_sesion_no_hay_datos_de_pacientes(self):
        anonimo = Cliente(self.base)
        for ruta in ("/api/datos", "/api/auditoria"):
            codigo, r = anonimo.pedir(ruta)
            self.assertEqual((codigo, r["codigo"]), (401, "sin_sesion"))
        self.assertEqual(anonimo.pedir("/api/pacientes", {"nombre": "Ana Gil"})[0], 401)

    def test_pin_incorrecto_no_revela_nada(self):
        codigo, r = Cliente(self.base).entrar("Rosa (recepción)", "0000")
        self.assertEqual(codigo, 400)
        self.assertEqual(r["error"], "Usuario o PIN incorrecto.")

    def test_cerrar_sesion(self):
        c = Cliente(self.base)
        c.entrar("Recepción Sur")
        self.assertEqual(c.pedir("/api/datos")[0], 200)
        c.pedir("/api/logout", {})
        self.assertEqual(c.pedir("/api/datos")[0], 401)

    def test_rechaza_peticiones_que_no_son_json(self):
        codigo, _ = self.rosa.pedir("/api/citas", b"paciente_id=1", tipo="application/x-www-form-urlencoded")
        self.assertEqual(codigo, 415)

    # ---- roles
    def test_recepcion_no_ve_contacto_ni_historial(self):
        _, d = self.rosa.pedir("/api/datos")
        self.assertNotIn("telefono", d["pacientes"][0])
        self.assertNotIn("correo", d["pacientes"][0])
        self.assertEqual(self.rosa.pedir("/api/auditoria")[0], 403)

    def test_doctora_ve_detalle_e_historial(self):
        self.dra.pedir("/api/pacientes", {"nombre": "Paciente Con Datos", "fecha_nacimiento": "1990-01-01",
                                          "tipo_doc": "cedula", "documento": "1710034065", "telefono": "0991234567"})
        _, d = self.dra.pedir("/api/datos")
        p = [x for x in d["pacientes"] if x["nombre"] == "Paciente Con Datos"][0]
        self.assertEqual((p["documento"], p["telefono"]), ("1710034065", "0991234567"))
        codigo, r = self.dra.pedir("/api/auditoria")
        self.assertEqual(codigo, 200)
        self.assertTrue(any(e["accion"] == "crear" and e["usuario"] == "Dra. Paredes" for e in r["eventos"]))

    def test_recepcion_ve_el_documento_enmascarado(self):
        _, d = self.rosa.pedir("/api/datos")
        con_doc = [p for p in d["pacientes"] if p["documento"]]
        for p in con_doc:
            self.assertTrue(p["documento"].startswith("******"))

    def test_quien_hizo_el_cambio_sale_de_la_sesion_no_del_navegador(self):
        self.rosa.pedir("/api/pacientes", {"nombre": "Prueba Suplantacion", "fecha_nacimiento": "1999-09-09",
                                           "usuario": "Dra. Paredes"})
        _, r = self.dra.pedir("/api/auditoria")
        evento = [e for e in r["eventos"] if e["accion"] == "crear" and e["entidad_id"] is not None][0]
        self.assertEqual(evento["usuario"], "Rosa (recepción)")

    # ---- reglas de negocio a través de la web
    def test_paciente_invalido_devuelve_mensaje_y_campo(self):
        codigo, r = self.rosa.pedir("/api/pacientes", {"nombre": "Luis Mora", "fecha_nacimiento": "2090-01-01"})
        self.assertEqual(codigo, 400)
        self.assertEqual(r["campo"], "fecha_nacimiento")
        self.assertIn("futura", r["error"])

    def test_cuerpo_basura(self):
        codigo, _ = self.rosa.pedir("/api/citas", b"{no es json")
        self.assertEqual(codigo, 400)

    def test_agendar_horario_y_reintento_sin_duplicar(self):
        _, d = self.rosa.pedir("/api/datos")
        pac, med = d["pacientes"][0]["id"], d["medicos"][0]["id"]
        centro = [c for c in d["clinicas"] if "Centro" in c["nombre"]][0]["id"]
        sur = [c for c in d["clinicas"] if "Sur" in c["nombre"]][0]["id"]
        base = {"paciente_id": pac, "medico_id": med, "clinica_id": centro, "fecha": "2031-03-03", "hora": "10:00"}  # lunes
        # el reintento con la misma clave devuelve la misma cita
        c1, r1 = self.rosa.pedir("/api/citas", {**base, "clave": "reintento-clinica-sur-1"})
        c2, r2 = self.rosa.pedir("/api/citas", {**base, "clave": "reintento-clinica-sur-1"})
        self.assertEqual((c1, c2, r1["id"]), (200, 200, r2["id"]))
        # Clínica Sur cierra a las 13:00
        codigo, r = self.rosa.pedir("/api/citas", {**base, "clinica_id": sur, "hora": "14:00"})
        self.assertEqual((codigo, r["campo"]), (400, "hora"))
        # cancelar dos veces no da error
        self.assertEqual(self.rosa.pedir(f"/api/citas/{r1['id']}/cancelar", {})[0], 200)
        _, otra = self.rosa.pedir(f"/api/citas/{r1['id']}/cancelar", {})
        self.assertIn("ya estaba", otra["mensaje"])


if __name__ == "__main__":
    unittest.main()
