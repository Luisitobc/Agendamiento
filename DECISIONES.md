# DECISIONES de MSLS+ / ClinAgenda

Este archivo dice **Que se resolvió y cómo, qué se dejó fuera y por qué, y qué se supuso** que la Dra. Paredes necesitaba.
Estado de cada punto: ✅ resuelto y con prueba automática , 🟡 resuelto a medias , ⬜ no resuelto (con el plan).

> **Para completar por el equipo antes del viernes** (solo ustedes pueden): integrantes y quién hizo qué, la prueba con una persona no técnica (sección 6), el cambio de plan (sección 8) y las preguntas enviadas a la clienta (sección 9). Están marcados con **[COMPLETAR]**.

## 0. Cómo se interpretó el encargo

"No sé de tecnología. Haz lo que creas que necesito." Se entendió que la clienta necesita, en este orden:
1. **Que ninguna cita se pierda ni se cruce** (su queja principal).
2. **Que doña Rosa pueda usarlo sin ayuda.**
3. **Que los datos de salud no los vea quien no debe** y que se sepa quién cambió qué.
4. **Que un mal día (internet caído, borrado accidental) no sea un desastre.**

Se prefirió **un sistema pequeño que funciona** sobre uno grande y roto. Tecnología: Python sin librerías externas + SQLite, para que un juez lo abra en 5 minutos sin instalar nada.

## 1. Sobre las citas

| Pregunta | Decisión | Estado |
|---|---|---|
| Dos recepcionistas piden la misma hora al mismo médico en el mismo segundo | Gana una sola; la otra recibe "Esa hora acaba de ser tomada…". Doble protección: la reserva ocurre dentro de una transacción que bloquea a otros escritores y, además, un **índice único en la base de datos** impide dos citas activas del mismo médico a la misma hora aunque el código fallara. Prueba con 10 hilos simultáneos: exactamente 1 gana. | ✅ |
| Cita en una fecha que ya pasó | Se rechaza con mensaje y se marca el casillero de fecha. | ✅ |
| Cancelar dos veces seguidas | No da error ni se duplica el registro: responde "ya estaba cancelada" y la auditoría registra una sola cancelación. | ✅ |
| La hora liberada por una cancelación | Queda libre de inmediato (la cita pasa a `cancelada`, el índice único solo mira las `activas`). Si alguien la ocupa, "Deshacer" ya no puede reactivar la cita y lo explica. | ✅ |
| ¿Un médico en dos clínicas a la misma hora? (Dr. Salazar) | No. La validación mira las citas del médico **en todas las clínicas**, con solapamiento (no solo hora exacta) y dice en cuál clínica ya está ocupado. | ✅ |
| 500 citas en un día / ninguna | El sistema solo acepta lo que cabe: con 12 médicos y turnos de 30 min en horario de clínica caben unas ~200 citas por día, y lo que exceda se rechaza con mensaje, no se pierde nada. Con 0 citas la pantalla dice "No hay citas próximas". No se hizo prueba de carga con 500 reales; a 400 pacientes al mes SQLite sobra. | 🟡 |
| Un paciente con dos citas a la misma hora | Se rechaza (no lo pidió la clienta; se consideró un error obvio). | ✅ |
| Horarios distintos por clínica | Tabla `horarios` por clínica y día. La cita debe **empezar y terminar** dentro del horario; si no, el mensaje dice el horario real. La pantalla muestra "Atiende: …" al elegir la clínica. | ✅ |
| Horarios de demostración | **Inventados** (Centro y Norte L–V 08:00–17:00 y sábado 08:00–12:00; Sur L–V 08:00–13:00). Hay que confirmarlos con la clienta. **No hay pantalla para editarlos**: hoy se cambian en la base. | 🟡 |
| Cambio de hora (horario de verano) | Ecuador no cambia la hora, así que las horas se guardan como hora local sin zona. Si el negocio se mudara a un país con cambio de hora, habría que guardar zona horaria. | 🟡 |
| Días de trabajo de cada médico (¿el Dr. Salazar trabaja lunes en Norte y martes en Centro?) | **No se modeló.** Hoy un médico puede recibir citas en cualquier clínica abierta; solo se impide que esté en dos sitios a la vez. Se necesita preguntar a la clienta su agenda semanal. | ⬜ |

## 2. Sobre las personas

| Pregunta | Decisión | Estado |
|---|---|---|
| ¿Es el mismo Juan Pérez de hace un mes? ¿Dos pacientes con el mismo nombre? | La identidad se apoya en el **documento** cuando existe (cédula o pasaporte, único). Sin documento, se compara **nombre normalizado + fecha de nacimiento**: si coincide, el sistema **pregunta** ("Es otra persona: crear igual") en vez de duplicar o mezclar en silencio. Dos Juan Pérez con distinta fecha de nacimiento conviven sin problema. | ✅ |
| Paciente sin cédula, correo ni teléfono | Se acepta: solo nombre y fecha de nacimiento son obligatorios. Para niños hay un campo "teléfono de un familiar o tutor". Opciones de documento: cédula, pasaporte o "todavía no tiene". | ✅ |
| Teléfono mal escrito | Se valida el **formato** (celular `09…` de 10 dígitos o fijo; acepta `+593`). **No se puede saber si el número existe**: eso solo se descubre al llamar. | 🟡 |
| ¿Quién ve qué? | Dos roles. **Recepción**: agenda y pacientes, documento enmascarado (`******1234`), **sin teléfonos ni correos**, sin historial. **Doctora**: todo. El rol se verifica **en el servidor** en cada petición, no solo en la pantalla. | ✅ |
| Contradicción de ese diseño | Una recepcionista a veces necesita el teléfono para confirmar una cita. Se eligió **el mínimo acceso** por defecto; si la clienta lo necesita, es un cambio de una línea (`ver_contacto` para recepción). **Pregunta pendiente para ella.** | 🟡 |
| Quién entra | Cada persona con **nombre y PIN** (4–12 números), guardado solo como huella (scrypt con sal). 5 errores seguidos = bloqueo de 5 minutos. El mensaje de error es igual si falla el usuario o el PIN. La sesión dura un turno (8 h) y viaja en una cookie `HttpOnly` + `SameSite=Strict`. | ✅ |
| PIN de demostración | Los usuarios de demo comparten el PIN **1234**: es solo para que el jurado pruebe. **Hay que cambiarlos** (`cambiar-pin`) antes de usar datos reales. | 🟡 |

## 3. Sobre los datos médicos

| Pregunta | Decisión | Estado |
|---|---|---|
| ¿Qué pasa si un ajeno los ve? | Nada se muestra sin iniciar sesión (probado: sin sesión, 401). Cada rol recibe solo los campos que le tocan (el servidor ni siquiera los envía a recepción). El servidor, por defecto, escucha solo en el propio equipo. Cabeceras de seguridad y escape de todo texto en pantalla. | ✅ |
| Si se abre a la red local o a Internet | **No hay HTTPS**: el PIN y los datos viajarían sin cifrar. Antes de exponerlo fuera del equipo hay que poner HTTPS (por ejemplo detrás de un proxy) o una VPN. No se resolvió por falta de tiempo; es lo primero que se haría. | ⬜ |
| ¿Quién cambió o borró una cita y cuándo? | Tabla `auditoria`: usuario, acción, cita/paciente y hora. El usuario sale **de la sesión**, nunca de lo que envíe el navegador (hay una prueba de suplantación). La doctora lo ve en **Historial de cambios**, incluidos los ingresos fallidos. Las citas **no se borran**: se cancelan. | ✅ |
| Límite de la auditoría | Quien tenga acceso directo al archivo de la base de datos podría alterarla. Protege contra errores y abusos desde la aplicación, no contra un administrador malintencionado. | 🟡 |
| Qué pasa si se borra todo | Copia automática de la base al arrancar y cada 6 horas (últimas 30, en `respaldos/`), hecha con el método de copia segura de SQLite aunque se esté usando. Restaurar = copiar un respaldo sobre `MSLS.db`. **Límite:** los respaldos están en el mismo disco; si el disco muere se pierden juntos. Falta copiarlos a otro equipo o a la nube. | 🟡 |
| Datos en el repositorio | Solo datos inventados. `.gitignore` excluye `*.db`, `respaldos/`, informes de importación y `.env`. **[COMPLETAR si por error se subió algo sensible, indicarlo aquí y corregirlo, como piden las bases.]** | ✅ |

## 4. Sobre el uso real

| Pregunta | Decisión | Estado |
|---|---|---|
| ¿Podría doña Rosa usarlo sin ayuda? | Diseñado para ello: letra de 18 px, botones grandes, una cosa por pantalla, mensajes en palabras normales, confirmación antes de cancelar. **No se ha probado con una persona real todavía** (ver sección 6). | 🟡 |
| Mensajes de error | Todos los errores de negocio están escritos para una persona ("La cédula debe tener 10 dígitos (escribió 9)"). Los errores técnicos nunca se muestran: la pantalla dice "Algo salió mal de nuestro lado. Sus datos no se perdieron" y el detalle queda en la consola. | ✅ |
| Botón equivocado: ¿se puede deshacer? | **Cancelar** se deshace con "Deshacer" (aviso de 12 s y botón en la lista). **Agendar** equivocado se corrige cancelando. **No hay editar ni eliminar paciente**: un error de tipeo en un nombre hoy no se puede corregir desde la pantalla. | 🟡 |
| ¿Celular? ¿Letra pequeña o poca luz? | Diseño adaptable: en celular el menú pasa abajo y los formularios a una columna. Letra base de 18 px. **No se midió el contraste ni se probó con poca luz.** | 🟡 |

## 5. Sobre los fallos

| Pregunta | Decisión | Estado |
|---|---|---|
| Clínica Sur: se cae el internet a mitad de agendar | Si la página **ya está abierta**, la cita se guarda **en ese equipo como PENDIENTE** con un aviso amarillo bien visible: *"SIN confirmar… la hora NO está reservada"*. Se reenvía sola al volver la conexión (y cada 30 s). Cada cita lleva una **clave única**: si el primer envío sí llegó pero la respuesta se perdió, el reintento **no duplica**. Si al reenviar la hora ya fue tomada, se avisa y una persona decide. | 🟡 |
| Límites de lo anterior | (a) Solo funciona con la página ya abierta: si se recarga sin internet, no carga (falta convertirla en app instalable con *service worker*). (b) Solo se encolan **citas nuevas**; pacientes nuevos, cancelaciones y deshacer exigen conexión. (c) Mientras está pendiente, otra recepcionista puede tomar esa hora. Se eligió **avisar con claridad** antes que fingir que está confirmada. | ⬜ |
| El sistema se cae un lunes a las 8 a.m. | Los datos no se pierden (transacciones + modo WAL). Se reinicia con `iniciar.bat`; las sesiones se pierden y hay que volver a entrar. **Falta** una agenda impresa del día como plan B en papel: la clienta ya usa un cuaderno, y habría que dejarle imprimir la agenda cada mañana. | ⬜ |

## 6. Prueba con una persona no técnica

**[COMPLETAR]** Probar con alguien que no sepa de tecnología (ideal: alguien de la edad de doña Rosa) la tarea *"agende una cita para mañana y luego cancélela"*, sin ayudar. Anotar aquí: quién fue, cuánto tardó, dónde dudó, qué se cambió después. Si no alcanza el tiempo, dejarlo escrito honestamente.

## 7. Sobre la calidad de los datos

| Pregunta | Decisión | Estado |
|---|---|---|
| Cédula de 9 dígitos, con letras, `0000000000` | Se rechazan con mensaje. Se valida: 10 dígitos solo del 0–9, provincia (01–24 o 30), tercer dígito < 6, no todos iguales, y **dígito verificador (módulo 10)**. | ✅ |
| ¿Cómo saber que es real y no solo lo parece? | **No se puede del todo**: el dígito verificador descarta errores de tipeo y números inventados al azar, pero una cédula bien formada puede no pertenecer a nadie. Confirmarlo exigiría consultar al Registro Civil. | 🟡 |
| Extranjero con pasaporte / sin documento | Opción **Pasaporte** (5–15 letras o números) y opción **"Todavía no tiene"**. | ✅ |
| Teléfono `12345`, correo `juan@@gmail` | Se rechazan con mensaje que dice cómo corregirlo. Teléfono y correo son opcionales. | ✅ |
| Nacido en 2090 o con 300 años | Se rechaza: no puede ser futuro ni pasar de 120 años. | ✅ |
| "MARÍA lópez" vs "Maria Lopez" | Se guarda presentable ("María López", respetando "de", "del", "la"…) y se compara sin tildes ni mayúsculas ni espacios de más. Se considera el mismo paciente **solo si además coincide la fecha de nacimiento**; entonces se pregunta. | ✅ |
| Mensajes al rechazar | Cada error dice **qué casillero** está mal, lo marca en rojo y **no borra lo escrito**. | ✅ |
| Texto raro (emojis, comillas, código) | El nombre solo admite letras. Todo lo guardado se escapa antes de mostrarse (sin inyección en pantalla) y la base usa consultas parametrizadas (sin inyección SQL). Probado con `'; DROP TABLE…`, `<script>`, emojis. | ✅ |

## 8. Datos de prueba del martes y cambio de plan

- **Importador** (`Codigos_Python/importar.py`): lee CSV con distintos separadores y codificaciones y títulos de columna variados, **nunca se detiene por una fila mala**, y produce un **informe** fila por fila. Reglas: un dato opcional malo (teléfono, correo, cédula) **no impide guardar al paciente**; se deja en blanco y se informa el valor original. Nombre o fecha inválidos → fila rechazada. Posibles duplicados → **se listan para revisar, no se mezclan solos**. Solo se arreglan errores seguros: el **0 inicial que Excel borra** (cédulas y celulares), y la cédula únicamente si con ese 0 queda válida. Por defecto trabaja en **modo prueba** sobre una copia: no guarda nada hasta usar `--aplicar`.
- **Falta** adaptarlo al archivo real: se probó solo con un CSV inventado. Hoy importa **pacientes**; médicos y citas pasadas **[COMPLETAR cuando llegue el archivo]**.
- **Cambio de plan (miércoles/jueves): [COMPLETAR]** qué pidió la clienta, qué se decidió, en qué rama/commits se ve.

## 9. Lo que la clienta no dijo

Cosas que un sistema así necesita y que ella no mencionó:
- **Recordatorios** por WhatsApp o llamada (hoy se pierden citas porque nadie recuerda al paciente). ⬜
- **Pacientes que no llegan** (inasistencias) y **lista de espera** para llenar horas liberadas. ⬜
- **Bloquear la agenda** de un médico por vacaciones, feriados o emergencias. ⬜
- **Editar un paciente** y **reprogramar** una cita en un solo paso (hoy: cancelar y agendar). ⬜
- **Consentimiento y cumplimiento legal**: son datos de salud, que la ley de protección de datos personales de Ecuador trata como sensibles; habría que revisar con un abogado qué consentimiento firmar y por cuánto tiempo guardarlos. ⬜
- **Quién administra** el sistema (cambiar PIN, dar de baja a una recepcionista que se va). Hoy es por línea de comandos; una pantalla para la doctora sería lo correcto. 🟡

## 10. Contradicción en lo que pidió

> *"Que cualquiera pueda cancelar una cita fácilmente"* **vs** *"no soporto que se pierdan citas"*.

Son opuestos si "cancelar" significa "borrar". **Decisión:** cancelar es **fácil pero reversible y registrado**: un botón, una confirmación, un "Deshacer" visible, **nada se borra** (la cita queda tachada), y la auditoría dice quién canceló y cuándo. "Cualquiera" se entendió como **cualquier persona del personal con sesión iniciada** (no cualquier persona de la calle): cancelar exige entrar, porque si no, "no se pierden citas" sería imposible de garantizar.

Preguntar a la clienta si el **paciente** debería poder cancelar por su cuenta (enlace por WhatsApp, por ejemplo). Eso abriría un nuevo problema de seguridad, así que no se construyó.

## 11. Preguntas para la clienta

**[COMPLETAR]** con lo que realmente se preguntó por el canal y lo que respondió. Preguntas que quedaron pendientes:
1. ¿Cuál es el horario real de cada clínica (incluidos sábados y feriados)?
2. ¿Qué días trabaja cada médico en cada clínica? (sobre todo el Dr. Salazar)
3. ¿La recepcionista necesita ver el teléfono del paciente?
4. ¿Cuánto dura una consulta normal? ¿Hay tipos de cita de distinta duración?
5. ¿Qué debe pasar si un paciente no llega? ¿Y si llega tarde?
6. ¿Dónde estará el computador principal y cómo es la red de cada clínica?
7. ¿Quién será la persona responsable de los respaldos y de los PIN?

## 12. Equipo y reparto

**[COMPLETAR]** Integrante — qué parte hizo — commits que lo muestran. Todos deben poder explicar su parte en la defensa.
