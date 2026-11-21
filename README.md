# MSLS+ · ClinAgenda — Agenda de citas para las clínicas de la Dra. Paredes

Sistema de citas para **Clínica Centro, Norte y Sur**, hecho para el Concurso de Programación ClinAgenda (Seventech).
Python (solo biblioteca estándar, **no hay que instalar nada**) + SQLite + HTML/CSS/JS.

> Las decisiones, lo que se dejó fuera y lo que se supuso están en **[DECISIONES.md](DECISIONES.md)**.

## Probarlo en 5 minutos

Necesita **Python 3.10 o más nuevo** (`python --version`).

1. Descargue o clone el repositorio.
2. **Windows:** doble clic en `iniciar.bat`. **Otro sistema:** `python Servidor/servidor.py` y abrir <http://localhost:8000>.
3. Entre con un usuario de demostración (PIN **1234** para todos):

   | Usuario | Rol | Qué ve |
   |---|---|---|
   | Rosa (recepción) | Recepción | Agenda, pacientes (documento enmascarado), sin teléfonos ni correos, sin historial |
   | Recepción Sur | Recepción | Igual que Rosa |
   | Dra. Paredes | Doctora | Todo, incluido contacto completo y **Historial de cambios** |

4. Recorrido sugerido para el jurado:
   1. **Nueva cita** → elija paciente, médico, clínica, fecha y hora → *Agendar*.
   2. Repita con **otro paciente, mismo médico, misma hora** → el sistema lo rechaza y explica por qué.
   3. Pruebe el mismo médico en **otra clínica** a una hora que se solape → rechazado (nombra la clínica donde ya está ocupado).
   4. Pruebe **Clínica Sur a las 15:00** → cierra a las 13:00, lo dice.
   5. **Citas** → *Cancelar cita* → aparece **Deshacer**. Cancele dos veces: no pasa nada malo.
   6. **Pacientes** → cédula `0000000000`, `123`, o nombre `😀😀` → mensaje claro, **sin borrar lo escrito**. Cédula válida de ejemplo: `1710034065`.
   7. Salga y entre como **Dra. Paredes** → aparece **Historial de cambios** con quién hizo cada cosa.
   8. PIN incorrecto 5 veces → bloqueo de 5 minutos.

## Pruebas automáticas (46 + 13)

```
cd Codigos_Python && python -m unittest -v      # reglas de negocio, validaciones, importador, concurrencia
cd Servidor       && python -m unittest -v      # servidor real: sesión, roles, reintentos
```

## Estructura

```
Codigos_Python/   nucleo.py (reglas), importar.py (datos sucios), pruebas
Servidor/         servidor.py (web + sesiones + respaldos), pruebas
estructura_html/  index.html y dibujos (la pantalla)
datos_ejemplo/    CSV inventado con errores típicos para probar el importador
MSLS.db           base de datos (se crea sola; no se sube a Git)
respaldos/        copias automáticas (no se suben a Git)
```

## Operación

- **Respaldos:** al arrancar y cada 6 horas, en `respaldos/` (se conservan las últimas 30). Para **restaurar**: detener el servidor, copiar un respaldo sobre `MSLS.db`, volver a iniciar.
- **Cambiar PIN / crear usuarios** (los de demostración deben cambiarse antes de usar datos reales):
  ```
  python Servidor/servidor.py cambiar-pin "Dra. Paredes"
  python Servidor/servidor.py usuario-nuevo "Nombre Apellido" recepcion
  ```
- **Importar pacientes de un CSV** (primero en modo prueba, no guarda nada):
  ```
  python Codigos_Python/importar.py datos_ejemplo/pacientes_sucios_ejemplo.csv
  python Codigos_Python/importar.py datos_ejemplo/pacientes_sucios_ejemplo.csv --aplicar
  ```
- **Otro equipo de la red:** por defecto solo escucha en `127.0.0.1`. Para abrirlo a la red local: `CLINAGENDA_HOST=0.0.0.0` (ver advertencias de HTTPS en DECISIONES.md).

## Datos y seguridad

Todos los datos del repositorio son **inventados**. No se suben datos reales, contraseñas ni llaves: `.gitignore` excluye bases de datos, respaldos e informes de importación.

## Uso de IA

Se usó inteligencia artificial como ayuda para escribir código y documentos; el equipo revisó y puede explicar cada parte.

## Cierre del concurso

El viernes por la noche, con todo subido:

```
git tag entrega-final
git push origin entrega-final
```
(o crear un *Release* llamado `entrega-final` en GitHub). Solo cuenta lo que esté en esa etiqueta.
