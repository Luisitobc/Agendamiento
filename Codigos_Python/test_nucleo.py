"""Ejecutar con:  python -m unittest -v"""
import unittest
from datetime import datetime, date

import nucleo as n

AHORA = datetime(2026, 11, 20, 12, 0)
HOY = date(2026, 11, 20)
LUNES_9 = "2026-11-23 09:00"


class Base(unittest.TestCase):
    def setUp(self):
        self.con = n.conectar()
        self.con.execute("INSERT INTO clinicas (id, nombre) VALUES (1,'Clínica Centro'),(2,'Clínica Norte')")
        self.con.execute("INSERT INTO medicos (id, nombre) VALUES (1,'Dr. Salazar')")
        self.p1 = n.registrar_paciente(self.con, "rosa", "Juan Pérez", "1980-05-10", hoy=HOY)
        self.p2 = n.registrar_paciente(self.con, "rosa", "Ana Torres", "1990-01-02", hoy=HOY)


class TestValidaciones(unittest.TestCase):
    def test_cedula_valida(self):
        self.assertEqual(n.validar_cedula("1710034065"), "1710034065")

    def test_cedula_invalida(self):
        for c in ["1710034064", "000000000", "0000000000", "17100340AB", "1111111111", ""]:
            with self.assertRaises(n.ErrorNegocio, msg=c):
                n.validar_cedula(c)

    def test_nombres_equivalentes(self):
        self.assertEqual(n.clave_nombre("  MARÍA   lópez "), n.clave_nombre("Maria Lopez"))

    def test_cedula_con_digitos_raros_no_rompe(self):
        for c in ["²²²²²²²²²²", "١٧١٠٠٣٤٠٦٥", "171003406 ", "17-10034065"]:
            with self.assertRaises(n.ErrorNegocio, msg=c):
                n.validar_cedula(c)

    def test_nombre_de_una_sola_palabra_y_particulas(self):
        with self.assertRaises(n.ErrorNegocio):
            n.limpiar_nombre("Maria")
        self.assertEqual(n.limpiar_nombre("  maría DE los ángeles  perez "), "María de los Ángeles Perez")

    def test_errores_indican_el_campo(self):
        con = n.conectar()
        with self.assertRaises(n.ErrorNegocio) as e:
            n.registrar_paciente(con, "rosa", "Ana Díaz", "1990-01-01", "cedula", "1710034064", hoy=HOY)
        self.assertEqual(e.exception.campo, "documento")

    def test_nombre_con_basura(self):
        for x in ["😀😀😀", "'; DROP TABLE citas;--", "<script>x</script>", "12345"]:
            with self.assertRaises(n.ErrorNegocio, msg=x):
                n.limpiar_nombre(x)

    def test_telefono_y_correo(self):
        self.assertEqual(n.validar_telefono("+593 99 123 4567"), "0991234567")
        with self.assertRaises(n.ErrorNegocio):
            n.validar_telefono("12345")
        with self.assertRaises(n.ErrorNegocio):
            n.validar_correo("juan@@gmail")
        self.assertIsNone(n.validar_correo(""))

    def test_fechas_nacimiento(self):
        for f in ["2090-01-01", "1700-01-01", "31/12/1990", "basura"]:
            with self.assertRaises(n.ErrorNegocio, msg=f):
                n.validar_fecha_nacimiento(f, HOY)


class TestPacientes(Base):
    def test_paciente_sin_nada_de_contacto(self):
        pid = n.registrar_paciente(self.con, "rosa", "Niño Sin Datos", "2018-03-03", hoy=HOY)
        self.assertGreater(pid, 0)

    def test_homonimo_pide_confirmacion(self):
        with self.assertRaises(n.ErrorNegocio):
            n.registrar_paciente(self.con, "rosa", "JUAN  pérez", "1980-05-10", hoy=HOY)
        n.registrar_paciente(self.con, "rosa", "JUAN  pérez", "1980-05-10",
                             confirmar_homonimo=True, hoy=HOY)

    def test_documento_repetido(self):
        n.registrar_paciente(self.con, "rosa", "Luis Mora", "1970-01-01", "cedula", "1710034065", hoy=HOY)
        with self.assertRaises(n.ErrorNegocio):
            n.registrar_paciente(self.con, "rosa", "Otro Nombre", "1971-01-01", "cedula", "1710034065", hoy=HOY)


class TestCitas(Base):
    def agendar(self, pac=None, clinica=1, inicio=LUNES_9):
        return n.agendar_cita(self.con, "rosa", pac or self.p1, 1, clinica, inicio, ahora=AHORA)

    def test_cita_en_el_pasado(self):
        with self.assertRaises(n.ErrorNegocio):
            self.agendar(inicio="2026-11-19 09:00")

    def test_misma_hora_mismo_medico(self):
        self.agendar()
        with self.assertRaises(n.ErrorNegocio):
            self.agendar(pac=self.p2)

    def test_medico_en_dos_clinicas_a_la_vez(self):
        self.agendar(clinica=1)
        with self.assertRaises(n.ErrorNegocio) as e:
            self.agendar(pac=self.p2, clinica=2, inicio="2026-11-23 09:15")  # se solapa
        self.assertIn("Clínica Centro", str(e.exception))

    def test_medico_dado_de_baja_no_recibe_citas(self):
        self.con.execute("UPDATE medicos SET activo=0 WHERE id=1")
        with self.assertRaises(n.ErrorNegocio):
            self.agendar()

    def test_citas_seguidas_no_chocan(self):
        self.agendar()
        self.agendar(pac=self.p2, inicio="2026-11-23 09:30")

    def test_cancelar_dos_veces_y_liberar_hora(self):
        cid = self.agendar()
        self.assertIn("cancelada", n.cancelar_cita(self.con, "rosa", cid))
        self.assertIn("ya estaba", n.cancelar_cita(self.con, "rosa", cid))
        n_aud = self.con.execute("SELECT COUNT(*) FROM auditoria WHERE accion='cancelar'").fetchone()[0]
        self.assertEqual(n_aud, 1)          # la segunda vez no ensucia la auditoría
        self.agendar(pac=self.p2)           # la hora quedó libre

    def test_deshacer_cancelacion(self):
        cid = self.agendar()
        n.cancelar_cita(self.con, "rosa", cid)
        self.assertEqual(n.deshacer_cancelacion(self.con, "rosa", cid, ahora=AHORA), "Cita reactivada.")
        n.cancelar_cita(self.con, "rosa", cid)
        self.agendar(pac=self.p2)           # alguien ocupa la hora
        with self.assertRaises(n.ErrorNegocio):
            n.deshacer_cancelacion(self.con, "rosa", cid, ahora=AHORA)

    def test_la_base_de_datos_tambien_protege(self):
        """Aunque el código fallara, el índice único impide la doble reserva."""
        self.agendar()
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute(
                "INSERT INTO citas (paciente_id, medico_id, clinica_id, inicio, fin, creado_por, creado_en)"
                " VALUES (2,1,1,'2026-11-23T09:00','2026-11-23T09:30','x','x')")

    def test_auditoria_registra_quien(self):
        cid = self.agendar()
        fila = self.con.execute("SELECT usuario, accion FROM auditoria WHERE entidad='cita' AND entidad_id=?",
                                (cid,)).fetchone()
        self.assertEqual((fila["usuario"], fila["accion"]), ("rosa", "agendar"))


class TestHorarios(Base):
    def setUp(self):
        super().setUp()
        # Clínica Centro: lunes a viernes 08:00-17:00. Clínica Norte: solo lunes 08:00-12:00.
        for dia in range(5):
            self.con.execute("INSERT INTO horarios VALUES (1,?,'08:00','17:00')", (dia,))
        self.con.execute("INSERT INTO horarios VALUES (2,0,'08:00','12:00')")

    def agendar(self, clinica, inicio, dur=30):
        return n.agendar_cita(self.con, "rosa", self.p1, 1, clinica, inicio, duracion_min=dur, ahora=AHORA)

    def test_dentro_del_horario(self):
        self.agendar(1, "2026-11-23 16:30")          # termina justo a las 17:00

    def test_antes_de_abrir_y_despues_de_cerrar(self):
        for hora in ["2026-11-23 07:30", "2026-11-23 16:45", "2026-11-23 17:00"]:
            with self.assertRaises(n.ErrorNegocio, msg=hora) as e:
                self.agendar(1, hora)
            self.assertEqual(e.exception.campo, "hora")

    def test_dia_que_la_clinica_no_atiende(self):
        with self.assertRaises(n.ErrorNegocio) as e:
            self.agendar(1, "2026-11-28 09:00")          # sábado
        self.assertIn("sábados", str(e.exception))
        self.assertEqual(e.exception.campo, "fecha")

    def test_clinicas_con_horarios_distintos(self):
        self.agendar(2, "2026-11-23 10:00")              # lunes en Norte: sí
        with self.assertRaises(n.ErrorNegocio):
            self.agendar(2, "2026-11-24 10:00")          # martes en Norte: no atiende

    def test_cita_que_cruza_la_medianoche(self):
        with self.assertRaises(n.ErrorNegocio):
            self.agendar(1, "2026-11-23 16:50", dur=240)

    def test_sin_horario_cargado_no_se_restringe(self):
        self.con.execute("INSERT INTO clinicas (id, nombre) VALUES (3,'Clínica Sur')")
        self.agendar(3, "2026-11-28 23:00")


class TestReintentos(Base):
    def test_misma_clave_no_duplica_la_cita(self):
        a = n.agendar_cita(self.con, "rosa", self.p1, 1, 1, LUNES_9, ahora=AHORA, clave="intento-0001-abcd")
        b = n.agendar_cita(self.con, "rosa", self.p1, 1, 1, LUNES_9, ahora=AHORA, clave="intento-0001-abcd")
        self.assertEqual(a, b)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM citas").fetchone()[0], 1)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM auditoria WHERE accion='agendar'").fetchone()[0], 1)

    def test_clave_invalida(self):
        with self.assertRaises(n.ErrorNegocio):
            n.agendar_cita(self.con, "rosa", self.p1, 1, 1, LUNES_9, ahora=AHORA, clave="x'; DROP TABLE citas;--")

    def test_base_antigua_sin_columna_clave_se_migra(self):
        import sqlite3, tempfile, os
        ruta = os.path.join(tempfile.mkdtemp(), "vieja.db")
        viejo = sqlite3.connect(ruta)
        viejo.executescript("CREATE TABLE citas (id INTEGER PRIMARY KEY, paciente_id INT, medico_id INT, clinica_id INT,"
                            " inicio TEXT, fin TEXT, estado TEXT DEFAULT 'activa', creado_por TEXT, creado_en TEXT);")
        viejo.close()
        con = n.conectar(ruta)
        self.assertIn("clave", [r[1] for r in con.execute("PRAGMA table_info(citas)")])


class TestUsuarios(unittest.TestCase):
    def setUp(self):
        self.con = n.conectar()
        n.crear_usuario(self.con, "Rosa", "recepcion", "1234")
        n.crear_usuario(self.con, "Dra. Paredes", "doctora", "9876")

    def test_login_correcto_devuelve_rol(self):
        self.assertEqual(n.iniciar_sesion(self.con, "Rosa", "1234"), {"nombre": "Rosa", "rol": "recepcion"})

    def test_el_pin_no_se_guarda_en_claro(self):
        guardado = self.con.execute("SELECT pin_hash FROM usuarios WHERE nombre='Rosa'").fetchone()[0]
        self.assertNotIn("1234", guardado)

    def test_error_igual_para_usuario_o_pin_malo(self):
        mensajes = set()
        for nombre, pin in [("Rosa", "0000"), ("NoExiste", "1234")]:
            with self.assertRaises(n.ErrorNegocio) as e:
                n.iniciar_sesion(self.con, nombre, pin)
            mensajes.add(str(e.exception))
        self.assertEqual(len(mensajes), 1)

    def test_bloqueo_tras_cinco_errores_y_se_libera(self):
        ahora = datetime(2026, 11, 20, 8, 0)
        for _ in range(n.INTENTOS_MAX):
            with self.assertRaises(n.ErrorNegocio):
                n.iniciar_sesion(self.con, "Rosa", "0000", ahora=ahora)
        with self.assertRaises(n.ErrorNegocio) as e:      # ahora ni el PIN correcto entra
            n.iniciar_sesion(self.con, "Rosa", "1234", ahora=ahora)
        self.assertIn("Demasiados intentos", str(e.exception))
        despues = datetime(2026, 11, 20, 8, n.BLOQUEO_MIN + 1)
        self.assertEqual(n.iniciar_sesion(self.con, "Rosa", "1234", ahora=despues)["rol"], "recepcion")

    def test_pin_invalido_al_crear(self):
        for pin in ["12", "abcd", "1234567890123", ""]:
            with self.assertRaises(n.ErrorNegocio, msg=pin):
                n.crear_usuario(self.con, "Otra Persona", "recepcion", pin)

    def test_permisos_por_rol(self):
        self.assertFalse(n.puede("recepcion", "ver_auditoria"))
        self.assertFalse(n.puede("recepcion", "ver_contacto"))
        self.assertTrue(n.puede("doctora", "ver_auditoria"))
        self.assertFalse(n.puede("inventado", "agendar"))

    def test_auditoria_registra_ingresos_fallidos(self):
        with self.assertRaises(n.ErrorNegocio):
            n.iniciar_sesion(self.con, "Rosa", "0000")
        acciones = [e["accion"] for e in n.listar_auditoria(self.con)]
        self.assertIn("ingreso_fallido", acciones)


class TestConcurrencia(unittest.TestCase):
    def test_diez_recepcionistas_a_la_misma_hora_solo_una_gana(self):
        """Simula 'mismo segundo, mismo médico': 10 hilos, cada uno con su propia conexión."""
        import os, tempfile, threading
        ruta = os.path.join(tempfile.mkdtemp(), "concurrencia.db")
        con = n.conectar(ruta)
        con.execute("INSERT INTO clinicas (id, nombre) VALUES (1,'Clínica Centro')")
        con.execute("INSERT INTO medicos (id, nombre) VALUES (1,'Dr. Salazar')")
        pacientes = [n.registrar_paciente(con, "t", f"Paciente Numero{chr(97 + i)}", "1990-01-01", hoy=HOY)
                     for i in range(10)]
        resultados, barrera = [], threading.Barrier(10)

        def intentar(pid):
            c = n.conectar(ruta)
            barrera.wait()                      # todos disparan a la vez
            try:
                n.agendar_cita(c, f"recepcion{pid}", pid, 1, 1, LUNES_9, ahora=AHORA)
                resultados.append("ok")
            except n.ErrorNegocio:
                resultados.append("rechazada")
            finally:
                c.close()

        hilos = [threading.Thread(target=intentar, args=(p,)) for p in pacientes]
        [h.start() for h in hilos]
        [h.join() for h in hilos]
        self.assertEqual(resultados.count("ok"), 1)
        self.assertEqual(resultados.count("rechazada"), 9)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM citas").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
