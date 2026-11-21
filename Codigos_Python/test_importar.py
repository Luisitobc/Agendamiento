"""Ejecutar con:  python -m unittest -v"""  # Comando para ejecutar las pruebas en modo detallado.
import unittest  # Módulo estándar de Python para definir y correr pruebas unitarias.
from datetime import date  # Clase para representar fechas fijas en las pruebas.
from pathlib import Path  # Utilidad orientada a objetos para construir rutas de archivos de forma portable.

import importar as imp  # Importa el módulo de importación de pacientes con el alias `imp`.
import nucleo as n  # Importa el módulo principal de lógica y conexión con el alias `n`.

HOY = date(2026, 11, 20)  # Fecha de referencia fija para evitar dependencias del reloj del sistema.
EJEMPLO = Path(__file__).resolve().parent.parent / "datos_ejemplo" / "pacientes_sucios_ejemplo.csv"  # Ruta al CSV de ejemplo con datos sucios.


class TestImportar(unittest.TestCase):  # Agrupa las pruebas de lectura, validación y carga desde CSV.
    def setUp(self):  # Prepara el entorno de pruebas antes de cada método `test_*`.
        self.con = n.conectar()  # Abre una conexión nueva a la base de datos.
        self.columnas, self.filas = imp.leer_filas(EJEMPLO.read_bytes())  # Lee los bytes del CSV y extrae columnas y filas.

    def correr(self, aplicar):  # Método auxiliar para correr la importación en simulación o guardando cambios.
        return imp.importar_pacientes(self.con, self.columnas, self.filas, aplicar=aplicar, hoy=HOY)  # Ejecuta `importar_pacientes` y devuelve resumen e informe.

    def test_en_prueba_no_guarda_nada(self):  # Verifica que el modo de prueba (`aplicar=False`) no escriba en la base de datos.
        resumen, _ = self.correr(False)  # Ejecuta en modo simulación y obtiene el resumen.
        self.assertGreater(resumen["importados"], 0)  # Comprueba que se contabilicen registros importables.
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM pacientes").fetchone()[0], 0)  # Confirma que la tabla `pacientes` siga vacía.

    def test_aplicar_guarda_y_clasifica(self):  # Comprueba que al aplicar se guarden registros y se clasifiquen errores/duplicados.
        resumen, informe = self.correr(True)  # Ejecuta la importación real y guarda el informe por fila.
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM pacientes").fetchone()[0],  # Cuenta los pacientes insertados en la BD.
                         resumen["importados"] + resumen["con_avisos"])  # Compara contra el total de importados más los que tuvieron avisos.
        resultados = {fila[3].strip(): fila[1] for fila in informe}  # Mapea nombre de fila limpiado a su estado en el informe.
        self.assertEqual(resultados[" "], "rechazada")            # nombre inválido  # Verifica que un nombre vacío/espacio sea rechazado.
        self.assertEqual(resultados["Luis Mora"], "rechazada")        # nacido en 2090  # Verifica que una fecha de nacimiento futura sea rechazada.
        self.assertEqual(resultados["Juan  PÉREZ"], "posible duplicado")  # Verifica que un posible duplicado quede marcado como tal.

    def test_nombre_se_limpia_y_datos_malos_no_frenan_al_paciente(self):  # Comprueba limpieza de campos y tolerancia a datos inválidos no bloqueantes.
        self.correr(True)  # Ejecuta la importación aplicando los cambios.
        maria = self.con.execute("SELECT * FROM pacientes WHERE nombre_clave='maria lopez'").fetchone()  # Busca el registro de María López por clave normalizada.
        self.assertEqual(maria["nombre"], "María López")  # Verifica que el nombre quede limpio y con capitalización correcta.
        self.assertEqual(maria["telefono"], "0991234567")   # Excel le había borrado el 0  # Confirma recuperación del cero inicial del teléfono.
        self.assertIsNone(maria["correo"])                   # «maria@@gmail» se deja en blanco y se informa  # Verifica que un correo inválido quede en `None`.
        self.assertEqual(maria["documento"], "1710034065")  # Comprueba que el número de documento se haya conservado correctamente.

    def test_cedula_de_9_digitos_solo_se_arregla_si_el_digito_verificador_cuadra(self):  # Verifica corrección de cédulas de 9 dígitos según dígito verificador.
        self.assertEqual(imp._documento("987654321", []), ("ninguno", None))     # no cuadra: no se inventa  # Rechaza completar si el dígito verificador no cuadra.
        avisos = []  # Lista para recolectar los avisos generados durante la validación.
        # 0102030405 no es una cédula válida real; se prueba con una que sí: 0926687856 (0 inicial)  # Aclaración sobre el ejemplo de cédula válida.
        self.assertEqual(imp._documento("926687856", avisos), ("cedula", "0926687856"))  # Verifica que anteponga el `0` cuando la cédula es válida.
        self.assertTrue(any("0 inicial" in a for a in avisos))  # Confirma que se haya registrado el aviso sobre el cero inicial.

    def test_cedula_invalida_importa_sin_documento_y_avisa(self):  # Verifica que una cédula inválida no impida importar el paciente y genere aviso.
        _, informe = self.correr(True)  # Ejecuta la importación real y obtiene el informe.
        juan = [f for f in informe if f[3] == "Juan Pérez"][0]  # Busca la fila correspondiente a `"Juan Pérez"` en el informe.
        self.assertEqual(juan[1], "importada con avisos")  # Comprueba que el estado sea `"importada con avisos"`.
        self.assertIn("0000000000", juan[2])  # Verifica que el detalle del aviso mencione el documento inválido (`0000000000`).

    def test_fechas(self):  # Comprueba el parseo de fechas válidas y el rechazo de fechas inválidas.
        self.assertEqual(imp.parsear_fecha("27/03/1985"), "1985-03-27")  # Convierte formato `DD/MM/AAAA` a `AAAA-MM-DD`.
        self.assertEqual(imp.parsear_fecha("2-1-1990"), "1990-01-02")  # Convierte formato con guiones y sin ceros a la izquierda.
        for malo in ["27/03/85", "31/02/1990", "mañana", ""]:  # Lista de fechas mal formadas, inexistentes o vacías.
            with self.assertRaises(n.ErrorNegocio, msg=malo):  # Espera `ErrorNegocio` para cada caso inválido.
                imp.parsear_fecha(malo)  # Intenta parsear la fecha inválida.

    def test_codificacion_latin1_y_coma(self):  # Verifica lectura de archivos codificados en Latin-1 con tildes y separador coma.
        crudo = "Nombre,Nacimiento\nJosé Núñez,1990-01-01\n".encode("latin-1")  # Codifica un CSV de ejemplo en `latin-1`.
        columnas, filas = imp.leer_filas(crudo)  # Lee columnas y filas desde los bytes codificados.
        imp.importar_pacientes(self.con, columnas, filas, aplicar=True, hoy=HOY)  # Importa el registro en la base de datos.
        self.assertEqual(self.con.execute("SELECT nombre FROM pacientes").fetchone()[0], "José Núñez")  # Verifica que el nombre con tilde se guarde intacto.

    def test_falta_columna_obligatoria(self):  # Comprueba que se lance `ErrorNegocio` si faltan columnas obligatorias.
        with self.assertRaises(n.ErrorNegocio):  # Espera excepción de negocio al faltar columnas requeridas.
            imp.leer_filas(b"Telefono,Correo\n099,a@b.com\n")  # Envía un CSV sin columnas de nombre o nacimiento.


if __name__ == " main ":  # Condición de ejecución directa del archivo (tal como estaba en tu código).
    unittest.main()  # Ejecuta las pruebas de `unittest`.