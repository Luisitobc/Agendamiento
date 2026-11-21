"""Ejecutar con:  python -m unittest -v"""
import unittest
from datetime import date
from pathlib import Path

import importar as imp
import nucleo as n

HOY = date(2026, 11, 20)
EJEMPLO = Path(__file__).resolve().parent.parent / "datos_ejemplo" / "pacientes_sucios_ejemplo.csv"


class TestImportar(unittest.TestCase):
    def setUp(self):
        self.con = n.conectar()
        self.columnas, self.filas = imp.leer_filas(EJEMPLO.read_bytes())

    def correr(self, aplicar):
        return imp.importar_pacientes(self.con, self.columnas, self.filas, aplicar=aplicar, hoy=HOY)

    def test_en_prueba_no_guarda_nada(self):
        resumen, _ = self.correr(False)
        self.assertGreater(resumen["importados"], 0)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM pacientes").fetchone()[0], 0)

    def test_aplicar_guarda_y_clasifica(self):
        resumen, informe = self.correr(True)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM pacientes").fetchone()[0],
                         resumen["importados"] + resumen["con_avisos"])
        resultados = {fila[3].strip(): fila[1] for fila in informe}
        self.assertEqual(resultados[" "], "rechazada")            # nombre inválido
        self.assertEqual(resultados["Luis Mora"], "rechazada")        # nacido en 2090
        self.assertEqual(resultados["Juan  PÉREZ"], "posible duplicado")

    def test_nombre_se_limpia_y_datos_malos_no_frenan_al_paciente(self):
        self.correr(True)
        maria = self.con.execute("SELECT * FROM pacientes WHERE nombre_clave='maria lopez'").fetchone()
        self.assertEqual(maria["nombre"], "María López")
        self.assertEqual(maria["telefono"], "0991234567")   # Excel le había borrado el 0
        self.assertIsNone(maria["correo"])                   # «maria@@gmail» se deja en blanco y se informa
        self.assertEqual(maria["documento"], "1710034065")

    def test_cedula_de_9_digitos_solo_se_arregla_si_el_digito_verificador_cuadra(self):
        self.assertEqual(imp._documento("987654321", []), ("ninguno", None))     # no cuadra: no se inventa
        avisos = []
        # 0102030405 no es una cédula válida real; se prueba con una que sí: 0926687856 (0 inicial)
        self.assertEqual(imp._documento("926687856", avisos), ("cedula", "0926687856"))
        self.assertTrue(any("0 inicial" in a for a in avisos))

    def test_cedula_invalida_importa_sin_documento_y_avisa(self):
        _, informe = self.correr(True)
        juan = [f for f in informe if f[3] == "Juan Pérez"][0]
        self.assertEqual(juan[1], "importada con avisos")
        self.assertIn("0000000000", juan[2])

    def test_fechas(self):
        self.assertEqual(imp.parsear_fecha("27/03/1985"), "1985-03-27")
        self.assertEqual(imp.parsear_fecha("2-1-1990"), "1990-01-02")
        for malo in ["27/03/85", "31/02/1990", "mañana", ""]:
            with self.assertRaises(n.ErrorNegocio, msg=malo):
                imp.parsear_fecha(malo)

    def test_codificacion_latin1_y_coma(self):
        crudo = "Nombre,Nacimiento\nJosé Núñez,1990-01-01\n".encode("latin-1")
        columnas, filas = imp.leer_filas(crudo)
        imp.importar_pacientes(self.con, columnas, filas, aplicar=True, hoy=HOY)
        self.assertEqual(self.con.execute("SELECT nombre FROM pacientes").fetchone()[0], "José Núñez")

    def test_falta_columna_obligatoria(self):
        with self.assertRaises(n.ErrorNegocio):
            imp.leer_filas(b"Telefono,Correo\n099,a@b.com\n")


if __name__ == " main ":
    unittest.main()
