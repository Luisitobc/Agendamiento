"""Ejecutar con:  python -m unittest -v"""  # Comando para correr todas las pruebas en modo detallado.
import unittest  # Módulo estándar de Python para escribir y ejecutar pruebas unitarias.
from datetime import datetime, date  # Clases para manejar fechas y horas de prueba de forma controlada.

import nucleo as n  # Importa el módulo principal de lógica de negocio bajo el alias `n`.

AHORA = datetime(2026, 11, 20, 12, 0)  # Fecha y hora simulada del momento actual para pruebas deterministas.
HOY = date(2026, 11, 20)  # Fecha del día simulada para validar fechas de nacimiento y reglas diarias.
LUNES_9 = "2026-11-23 09:00"  # Fecha y hora de referencia (lunes a las 09:00) para agendar citas.


class Base(unittest.TestCase):  # Clase base que prepara un entorno limpio antes de cada prueba que herede de ella.
    def setUp(self):  # Se ejecuta automáticamente antes de cada método `test_*` en las subclases.
        self.con = n.conectar()  # Abre una conexión nueva a la base de datos (en memoria por defecto).
        self.con.execute("INSERT INTO clinicas (id, nombre) VALUES (1,'Clínica Centro'),(2,'Clínica Norte')")  # Inserta dos clínicas de prueba.
        self.con.execute("INSERT INTO medicos (id, nombre) VALUES (1,'Dr. Salazar')")  # Inserta un médico de prueba.
        self.p1 = n.registrar_paciente(self.con, "rosa", "Juan Pérez", "1980-05-10", hoy=HOY)  # Registra al primer paciente y guarda su ID.
        self.p2 = n.registrar_paciente(self.con, "rosa", "Ana Torres", "1990-01-02", hoy=HOY)  # Registra al segundo paciente y guarda su ID.


class TestValidaciones(unittest.TestCase):  # Agrupa pruebas unitarias sobre validaciones y limpieza de datos.
    def test_cedula_valida(self):  # Verifica que una cédula válida sea aceptada y retornada sin cambios.
        self.assertEqual(n.validar_cedula("1710034065"), "1710034065")  # Comprueba igualdad con el número esperado.

    def test_cedula_invalida(self):  # Verifica que cédulas inválidas lancen `ErrorNegocio`.
        for c in ["1710034064", "000000000", "0000000000", "17100340AB", "1111111111", ""]:  # Casos de cédulas inválidas.
            with self.assertRaises(n.ErrorNegocio, msg=c):  # Espera excepción de negocio mostrando el caso `c` si falla.
                n.validar_cedula(c)  # Ejecuta la validación sobre la cédula de prueba.

    def test_nombres_equivalentes(self):  # Comprueba que variaciones de mayúsculas, tildes y espacios generen la misma clave.
        self.assertEqual(n.clave_nombre("  MARÍA   lópez "), n.clave_nombre("Maria Lopez"))  # Compara ambas claves normalizadas.

    def test_cedula_con_digitos_raros_no_rompe(self):  # Asegura que caracteres numéricos no ASCII o formatos raros sean rechazados.
        for c in ["²²²²²²²²²²", "١٧١٠٠٣٤٠٦٥", "171003406 ", "17-10034065"]:  # Entradas con dígitos no estándar, espacios o guiones.
            with self.assertRaises(n.ErrorNegocio, msg=c):  # Espera que se lance `ErrorNegocio`.
                n.validar_cedula(c)  # Valida cada cadena problemática.

    def test_nombre_de_una_sola_palabra_y_particulas(self):  # Prueba rechazo de un solo nombre y formato con partículas ("de los").
        with self.assertRaises(n.ErrorNegocio):  # Espera error si solo se ingresa una palabra.
            n.limpiar_nombre("Maria")  # Intenta limpiar un nombre de una sola palabra.
        self.assertEqual(n.limpiar_nombre("  maría DE los ángeles  perez "), "María de los Ángeles Perez")  # Verifica capitalización y limpieza de espacios.

    def test_errores_indican_el_campo(self):  # Verifica que el error de negocio identifique qué campo falló.
        con = n.conectar()  # Crea una conexión independiente para esta prueba.
        with self.assertRaises(n.ErrorNegocio) as e:  # Captura la excepción lanzada en `e`.
            n.registrar_paciente(con, "rosa", "Ana Díaz", "1990-01-01", "cedula", "1710034064", hoy=HOY)  # Intenta registrar con cédula inválida.
        self.assertEqual(e.exception.campo, "documento")  # Confirma que el atributo `campo` sea `"documento"`.

    def test_nombre_con_basura(self):  # Comprueba que entradas vacías, SQLi, XSS o solo números sean rechazadas.
        for x in ["", "'; DROP TABLE citas;--", "<script>x</script>", "12345"]:  # Lista de entradas maliciosas o inválidas.
            with self.assertRaises(n.ErrorNegocio, msg=x):  # Espera `ErrorNegocio` para cada caso.
                n.limpiar_nombre(x)  # Intenta limpiar el texto de prueba.

    def test_telefono_y_correo(self):  # Valida normalización de teléfono y reglas de correo electrónico.
        self.assertEqual(n.validar_telefono("+593 99 123 4567"), "0991234567")  # Convierte formato internacional a local de 10 dígitos.
        with self.assertRaises(n.ErrorNegocio):  # Espera error para un teléfono demasiado corto/inválido.
            n.validar_telefono("12345")  # Prueba teléfono inválido.
        with self.assertRaises(n.ErrorNegocio):  # Espera error para un correo mal formado.
            n.validar_correo("juan@@gmail")  # Prueba correo con doble `@` y sin dominio válido.
        self.assertIsNone(n.validar_correo(""))  # Comprueba que un correo vacío retorne `None`.

    def test_fechas_nacimiento(self):  # Verifica que fechas de nacimiento futuras, antiguas o mal formateadas fallen.
        for f in ["2090-01-01", "1700-01-01", "31/12/1990", "basura"]:  # Fechas inválidas de prueba.
            with self.assertRaises(n.ErrorNegocio, msg=f):  # Espera `ErrorNegocio` en cada caso.
                n.validar_fecha_nacimiento(f, HOY)  # Valida la fecha contra `HOY`.


class TestPacientes(Base):  # Pruebas del registro de pacientes heredando datos base de `Base`.
    def test_paciente_sin_nada_de_contacto(self):  # Permite registrar paciente sin teléfono ni correo.
        pid = n.registrar_paciente(self.con, "rosa", "Niño Sin Datos", "2018-03-03", hoy=HOY)  # Registra paciente sin contacto.
        self.assertGreater(pid, 0)  # Verifica que se haya asignado un ID positivo.

    def test_homonimo_pide_confirmacion(self):  # Comprueba que un nombre repetido exija confirmación explícita.
        with self.assertRaises(n.ErrorNegocio):  # Sin `confirmar_homonimo=True` debe lanzar error.
            n.registrar_paciente(self.con, "rosa", "JUAN  pérez", "1980-05-10", hoy=HOY)  # Intento con mismo nombre que `p1`.
        n.registrar_paciente(self.con, "rosa", "JUAN  pérez", "1980-05-10",  # Reintenta con confirmación explícita.
                             confirmar_homonimo=True, hoy=HOY)  # Permite guardar el homónimo confirmado.

    def test_documento_repetido(self):  # Verifica que no se permita repetir el mismo documento en otro paciente.
        n.registrar_paciente(self.con, "rosa", "Luis Mora", "1970-01-01", "cedula", "1710034065", hoy=HOY)  # Registra cédula válida.
        with self.assertRaises(n.ErrorNegocio):  # Espera error al reutilizar la misma cédula.
            n.registrar_paciente(self.con, "rosa", "Otro Nombre", "1971-01-01", "cedula", "1710034065", hoy=HOY)  # Segundo paciente con igual cédula.


class TestCitas(Base):  # Pruebas de agendamiento, choques, cancelaciones, reactivación y auditoría.
    def agendar(self, pac=None, clinica=1, inicio=LUNES_9):  # Método auxiliar para agendar citas con valores por defecto.
        return n.agendar_cita(self.con, "rosa", pac or self.p1, 1, clinica, inicio, ahora=AHORA)  # Llama a `agendar_cita` y retorna el ID.

    def test_cita_en_el_pasado(self):  # Impide agendar citas en una fecha/hora pasada respecto a `AHORA`.
        with self.assertRaises(n.ErrorNegocio):  # Espera `ErrorNegocio`.
            self.agendar(inicio="2026-11-19 09:00")  # Intenta agendar un día antes de `AHORA`.

    def test_misma_hora_mismo_medico(self):  # Impide agendar dos citas a la misma hora con el mismo médico.
        self.agendar()  # Agenda la primera cita en `LUNES_9`.
        with self.assertRaises(n.ErrorNegocio):  # Espera error por choque de horario.
            self.agendar(pac=self.p2)  # Intenta agendar otro paciente a la misma hora y médico.

    def test_medico_en_dos_clinicas_a_la_vez(self):  # Impide que un médico atienda en dos clínicas en horarios superpuestos.
        self.agendar(clinica=1)  # Agenda cita en Clínica Centro (`id=1`) a las 09:00.
        with self.assertRaises(n.ErrorNegocio) as e:  # Captura el error por solapamiento entre clínicas.
            self.agendar(pac=self.p2, clinica=2, inicio="2026-11-23 09:15")  # se solapa  # Cita a las 09:15 en Clínica Norte (`id=2`).
        self.assertIn("Clínica Centro", str(e.exception))  # Verifica que el mensaje mencione la clínica donde ya tenía cita.

    def test_medico_dado_de_baja_no_recibe_citas(self):  # Verifica que un médico inactivo no pueda recibir nuevas citas.
        self.con.execute("UPDATE medicos SET activo=0 WHERE id=1")  # Marca al médico 1 como inactivo.
        with self.assertRaises(n.ErrorNegocio):  # Espera `ErrorNegocio`.
            self.agendar()  # Intenta agendar con el médico inactivo.

    def test_citas_seguidas_no_chocan(self):  # Comprueba que una cita que empieza al terminar otra no genera conflicto.
        self.agendar()  # Agenda de 09:00 a 09:30 (duración por defecto).
        self.agendar(pac=self.p2, inicio="2026-11-23 09:30")  # Agenda justo a las 09:30 con otro paciente.

    def test_cancelar_dos_veces_y_liberar_hora(self):  # Verifica cancelación, idempotencia al cancelar y liberación de cupo.
        cid = self.agendar()  # Agenda una cita y guarda su ID.
        self.assertIn("cancelada", n.cancelar_cita(self.con, "rosa", cid))  # Cancela la cita y verifica el mensaje.
        self.assertIn("ya estaba", n.cancelar_cita(self.con, "rosa", cid))  # Intenta cancelar por segunda vez la misma cita.
        n_aud = self.con.execute("SELECT COUNT(*) FROM auditoria WHERE accion='cancelar'").fetchone()[0]  # Cuenta eventos `'cancelar'` en auditoría.
        self.assertEqual(n_aud, 1)          # la segunda vez no ensucia la auditoría  # Confirma que solo se registró una cancelación.
        self.agendar(pac=self.p2)           # la hora quedó libre  # Verifica que ahora se puede agendar en ese horario.

    def test_deshacer_cancelacion(self):  # Prueba reactivar una cita cancelada y bloquear si el horario ya se ocupó.
        cid = self.agendar()  # Agenda una cita inicial.
        n.cancelar_cita(self.con, "rosa", cid)  # Cancela la cita.
        self.assertEqual(n.deshacer_cancelacion(self.con, "rosa", cid, ahora=AHORA), "Cita reactivada.")  # Reactiva la cita si sigue libre.
        n.cancelar_cita(self.con, "rosa", cid)  # Vuelve a cancelarla.
        self.agendar(pac=self.p2)           # alguien ocupa la hora  # Otro paciente toma ese horario.
        with self.assertRaises(n.ErrorNegocio):  # Espera error al intentar reactivar sobre un horario ocupado.
            n.deshacer_cancelacion(self.con, "rosa", cid, ahora=AHORA)  # Intenta deshacer la cancelación.

    def test_la_base_de_datos_tambien_protege(self):  # Comprueba restricción de unicidad directamente en SQLite.
        self.agendar()  # Inserta una cita activa en el horario de prueba.
        import sqlite3  # Importa `sqlite3` para comprobar `IntegrityError`.
        with self.assertRaises(sqlite3.IntegrityError):  # Espera error de integridad en la base de datos.
            self.con.execute(  # Ejecuta un `INSERT` directo saltándose la función de negocio.
                "INSERT INTO citas (paciente_id, medico_id, clinica_id, inicio, fin, creado_por, creado_en)"  # Columnas de la tabla `citas`.
                " VALUES (2,1,1,'2026-11-23T09:00','2026-11-23T09:30','x','x')")  # Valores que chocan con la cita ya existente.

    def test_auditoria_registra_quien(self):  # Verifica que la auditoría guarde el usuario y la acción realizada.
        cid = self.agendar()  # Agenda una cita como `"rosa"`.
        fila = self.con.execute("SELECT usuario, accion FROM auditoria WHERE entidad='cita' AND entidad_id=?",  # Consulta el registro de auditoría.
                                (cid,)).fetchone()  # Obtiene la fila correspondiente al ID de la cita.
        self.assertEqual((fila["usuario"], fila["accion"]), ("rosa", "agendar"))  # Comprueba usuario y acción esperados.


class TestHorarios(Base):  # Pruebas de reglas de atención por día de la semana y franja horaria.
    def setUp(self):  # Configura horarios de atención adicionales sobre la base inicial.
        super().setUp()  # Ejecuta el `setUp` de `Base`.
        # Clínica Centro: lunes a viernes 08:00-17:00. Clínica Norte: solo lunes 08:00-12:00.  # Resumen de horarios configurados.
        for dia in range(5):  # Días 0 a 4 (lunes a viernes).
            self.con.execute("INSERT INTO horarios VALUES (1,?,'08:00','17:00')", (dia,))  # Inserta horario para Clínica Centro.
        self.con.execute("INSERT INTO horarios VALUES (2,0,'08:00','12:00')")  # Inserta horario para Clínica Norte solo los lunes (día 0).

    def agendar(self, clinica, inicio, dur=30):  # Método auxiliar para agendar citas con duración personalizada.
        return n.agendar_cita(self.con, "rosa", self.p1, 1, clinica, inicio, duracion_min=dur, ahora=AHORA)  # Llama a `agendar_cita`.

    def test_dentro_del_horario(self):  # Verifica que una cita válida dentro del horario sea aceptada.
        self.agendar(1, "2026-11-23 16:30")          # termina justo a las 17:00  # Cita de 30 min que termina al cierre.

    def test_antes_de_abrir_y_despues_de_cerrar(self):  # Comprueba rechazo fuera de la franja horaria permitida.
        for hora in ["2026-11-23 07:30", "2026-11-23 16:45", "2026-11-23 17:00"]:  # Horas antes de abrir o que pasan del cierre.
            with self.assertRaises(n.ErrorNegocio, msg=hora) as e:  # Espera `ErrorNegocio` y captura la excepción.
                self.agendar(1, hora)  # Intenta agendar en esa hora.
            self.assertEqual(e.exception.campo, "hora")  # Verifica que el error apunte al campo `"hora"`.

    def test_dia_que_la_clinica_no_atiende(self):  # Verifica rechazo cuando la clínica no abre ese día.
        with self.assertRaises(n.ErrorNegocio) as e:  # Espera `ErrorNegocio`.
            self.agendar(1, "2026-11-28 09:00")          # sábado  # Intenta agendar en sábado (día 5).
        self.assertIn("sábados", str(e.exception))  # Comprueba que el mensaje mencione los sábados.
        self.assertEqual(e.exception.campo, "fecha")  # Verifica que el error apunte al campo `"fecha"`.

    def test_clinicas_con_horarios_distintos(self):  # Comprueba que cada clínica aplique su propio horario.
        self.agendar(2, "2026-11-23 10:00")              # lunes en Norte: sí  # Lunes dentro del horario de Clínica Norte.
        with self.assertRaises(n.ErrorNegocio):  # Espera error en un día no habilitado para esa clínica.
            self.agendar(2, "2026-11-24 10:00")          # martes en Norte: no atiende  # Martes en Clínica Norte.

    def test_cita_que_cruza_la_medianoche(self):  # Impide citas cuya duración cruce hacia el día siguiente.
        with self.assertRaises(n.ErrorNegocio):  # Espera `ErrorNegocio`.
            self.agendar(1, "2026-11-23 16:50", dur=240)  # Cita de 4 horas que cruzaría la medianoche.

    def test_sin_horario_cargado_no_se_restringe(self):  # Verifica comportamiento cuando una clínica no tiene horarios registrados.
        self.con.execute("INSERT INTO clinicas (id, nombre) VALUES (3,'Clínica Sur')")  # Agrega Clínica Sur sin filas en `horarios`.
        self.agendar(3, "2026-11-28 23:00")  # Agenda sin restricción de horario configurada.


class TestReintentos(unittest.TestCase):  # Pruebas de reintentos idempotentes y migración de esquema.
    def test_misma_clave_no_duplica_la_cita(self):  # Verifica que reenviar la misma `clave` no cree duplicados.
        a = n.agendar_cita(self.con, "rosa", self.p1, 1, 1, LUNES_9, ahora=AHORA, clave="intento-0001-abcd")  # Primer envío con clave.
        b = n.agendar_cita(self.con, "rosa", self.p1, 1, 1, LUNES_9, ahora=AHORA, clave="intento-0001-abcd")  # Reenvío con la misma clave.
        self.assertEqual(a, b)  # Verifica que se devuelva el mismo ID de cita.
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM citas").fetchone()[0], 1)  # Confirma que solo existe una cita.
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM auditoria WHERE accion='agendar'").fetchone()[0], 1)  # Confirma una sola auditoría de agendamiento.

    def test_clave_invalida(self):  # Rechaza claves de reintento con caracteres no válidos.
        with self.assertRaises(n.ErrorNegocio):  # Espera `ErrorNegocio`.
            n.agendar_cita(self.con, "rosa", self.p1, 1, 1, LUNES_9, ahora=AHORA, clave="x'; DROP TABLE citas;--")  # Intento con clave maliciosa.

    def test_base_antigua_sin_columna_clave_se_migra(self):  # Comprueba migración automática si falta la columna `clave`.
        import sqlite3, tempfile, os  # Módulos para crear una base temporal en disco.
        ruta = os.path.join(tempfile.mkdtemp(), "vieja.db")  # Ruta de un archivo SQLite temporal.
        viejo = sqlite3.connect(ruta)  # Abre conexión a la base antigua.
        viejo.executescript("CREATE TABLE citas (id INTEGER PRIMARY KEY, paciente_id INT, medico_id INT, clinica_id INT,"  # Crea tabla `citas` sin columna `clave`.
                            " inicio TEXT, fin TEXT, estado TEXT DEFAULT 'activa', creado_por TEXT, creado_en TEXT);")  # Resto de columnas antiguas.
        viejo.close()  # Cierra la conexión inicial.
        con = n.conectar(ruta)  # Conecta usando `n.conectar` para disparar la migración.
        self.assertIn("clave", [r[1] for r in con.execute("PRAGMA table_info(citas)")])  # Verifica que `clave` ahora exista en `citas`.


class TestUsuarios(unittest.TestCase):  # Pruebas de autenticación, seguridad del PIN, bloqueo y roles.
    def setUp(self):  # Prepara base y usuarios de ejemplo para pruebas de acceso.
        self.con = n.conectar()  # Abre conexión en memoria.
        n.crear_usuario(self.con, "Rosa", "recepcion", "1234")  # Crea usuario de recepción con PIN `"1234"`.
        n.crear_usuario(self.con, "Dra. Paredes", "doctora", "9876")  # Crea usuario doctora con PIN `"9876"`.

    def test_login_correcto_devuelve_rol(self):  # Verifica datos devueltos al iniciar sesión correctamente.
        self.assertEqual(n.iniciar_sesion(self.con, "Rosa", "1234"), {"nombre": "Rosa", "rol": "recepcion"})  # Comprueba nombre y rol.

    def test_el_pin_no_se_guarda_en_claro(self):  # Verifica que el PIN no quede almacenado en texto plano.
        guardado = self.con.execute("SELECT pin_hash FROM usuarios WHERE nombre='Rosa'").fetchone()[0]  # Obtiene `pin_hash` de Rosa.
        self.assertNotIn("1234", guardado)  # Asegura que el PIN en claro no esté en el hash.

    def test_error_igual_para_usuario_o_pin_malo(self):  # Evita filtrar si un usuario existe devolviendo el mismo error.
        mensajes = set()  # Conjunto para almacenar los mensajes de error obtenidos.
        for nombre, pin in [("Rosa", "0000"), ("NoExiste", "1234")]:  # Prueba PIN incorrecto y usuario inexistente.
            with self.assertRaises(n.ErrorNegocio) as e:  # Captura la excepción de negocio.
                n.iniciar_sesion(self.con, nombre, pin)  # Intenta iniciar sesión.
            mensajes.add(str(e.exception))  # Agrega el texto del error al conjunto.
        self.assertEqual(len(mensajes), 1)  # Confirma que ambos casos arrojaron exactamente el mismo mensaje.

    def test_bloqueo_tras_cinco_errores_y_se_libera(self):  # Comprueba bloqueo tras fallos repetidos y desbloqueo tras esperar.
        ahora = datetime(2026, 11, 20, 8, 0)  # Hora inicial de prueba.
        for _ in range(n.INTENTOS_MAX):  # Repite tantos intentos fallidos como permita `INTENTOS_MAX`.
            with self.assertRaises(n.ErrorNegocio):  # Espera error por PIN incorrecto.
                n.iniciar_sesion(self.con, "Rosa", "0000", ahora=ahora)  # Intento fallido con PIN `"0000"`.
        with self.assertRaises(n.ErrorNegocio) as e:      # ahora ni el PIN correcto entra  # Cuenta bloqueada temporalmente.
            n.iniciar_sesion(self.con, "Rosa", "1234", ahora=ahora)  # Intento con PIN correcto durante el bloqueo.
        self.assertIn("Demasiados intentos", str(e.exception))  # Verifica mensaje de bloqueo por exceso de intentos.
        despues = datetime(2026, 11, 20, 8, n.BLOQUEO_MIN + 1)  # Avanza el reloj más allá del tiempo de bloqueo.
        self.assertEqual(n.iniciar_sesion(self.con, "Rosa", "1234", ahora=despues)["rol"], "recepcion")  # Comprueba acceso tras liberarse.

    def test_pin_invalido_al_crear(self):  # Rechaza PINs con formato o longitud inválidos al crear usuario.
        for pin in ["12", "abcd", "1234567890123", ""]:  # PINs inválidos de prueba.
            with self.assertRaises(n.ErrorNegocio, msg=pin):  # Espera `ErrorNegocio`.
                n.crear_usuario(self.con, "Otra Persona", "recepcion", pin)  # Intenta crear usuario con PIN inválido.

    def test_permisos_por_rol(self):  # Comprueba permisos asignados a cada rol.
        self.assertFalse(n.puede("recepcion", "ver_auditoria"))  # Recepción no puede ver auditoría.
        self.assertFalse(n.puede("recepcion", "ver_contacto"))  # Recepción no puede ver datos de contacto restringidos.
        self.assertTrue(n.puede("doctora", "ver_auditoria"))  # Doctora sí puede ver auditoría.
        self.assertFalse(n.puede("inventado", "agendar"))  # Un rol inexistente no tiene permisos.

    def test_auditoria_registra_ingresos_fallidos(self):  # Verifica que los intentos fallidos de login queden auditados.
        with self.assertRaises(n.ErrorNegocio):  # Espera error por PIN incorrecto.
            n.iniciar_sesion(self.con, "Rosa", "0000")  # Intento de ingreso fallido.
        acciones = [e["accion"] for e in n.listar_auditoria(self.con)]  # Extrae las acciones registradas en auditoría.
        self.assertIn("ingreso_fallido", acciones)  # Comprueba que `"ingreso_fallido"` esté registrado.


class TestConcurrencia(unittest.TestCase):  # Pruebas de concurrencia con múltiples hilos simultáneos.
    def test_diez_recepcionistas_a_la_misma_hora_solo_una_gana(self):  # Verifica que solo una reserva prospere ante competencia simultánea.
        import os, tempfile, threading  # Módulos para archivo temporal en disco e hilos.
        ruta = os.path.join(tempfile.mkdtemp(), "concurrencia.db")  # Crea ruta temporal para la base de pruebas concurrentes.
        con = n.conectar(ruta)  # Abre conexión principal para inicializar datos.
        con.execute("INSERT INTO clinicas (id, nombre) VALUES (1,'Clínica Centro')")  # Inserta clínica de prueba.
        con.execute("INSERT INTO medicos (id, nombre) VALUES (1,'Dr. Salazar')")  # Inserta médico de prueba.
        pacientes = [n.registrar_paciente(con, "t", f"Paciente Numero{chr(97 + i)}", "1990-01-01", hoy=HOY)  # Crea 10 pacientes (`a` a `j`).
                     for i in range(10)]  # Lista con los 10 IDs de pacientes.
        resultados, barrera = [], threading.Barrier(10)  # Lista de resultados y barrera para sincronizar 10 hilos.

        def intentar(pid):  # Función ejecutada por cada hilo intentando agendar a la misma hora.
            c = n.conectar(ruta)  # Cada hilo abre su propia conexión a la base de datos.
            barrera.wait()                      # todos disparan a la vez  # Espera a que los 10 hilos estén listos.
            try:  # Intenta agendar la cita para el paciente `pid`.
                n.agendar_cita(c, f"recepcion{pid}", pid, 1, 1, LUNES_9, ahora=AHORA)  # Llama a `agendar_cita`.
                resultados.append("ok")  # Registra éxito si este hilo ganó el cupo.
            except n.ErrorNegocio:  # Captura rechazo por conflicto de horario o bloqueo.
                resultados.append("rechazada")  # Registra que la reserva fue rechazada.
            finally:  # Bloque que siempre se ejecuta al terminar el intento.
                c.close()  # Cierra la conexión del hilo.

        hilos = [threading.Thread(target=intentar, args=(p,)) for p in pacientes]  # Crea los 10 hilos.
        [h.start() for h in hilos]  # Inicia todos los hilos.
        [h.join() for h in hilos]  # Espera a que todos los hilos terminen.
        self.assertEqual(resultados.count("ok"), 1)  # Verifica que exactamente 1 cita haya sido agendada.
        self.assertEqual(resultados.count("rechazada"), 9)  # Verifica que las otras 9 hayan sido rechazadas.
        self.assertEqual(con.execute("SELECT COUNT(*) FROM citas").fetchone()[0], 1)  # Confirma que en BD hay 1 sola cita.


if __name__ == "__main__":  # Ejecuta las pruebas si el archivo se corre directamente como script.
    unittest.main()  # Arranca el corredor de pruebas de `unittest`.