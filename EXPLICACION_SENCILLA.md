# PQC-Bench TLS 1.3 explicado de forma sencilla

Guía para contar, sin tecnicismos, qué se construyó en este primer entregable, cómo funciona y qué encontramos. Sirve como apoyo para una exposición o para explicárselo a alguien que no es del área.

---

## 1. ¿Cuál es el problema?

Cada vez que entras a una página con candado (tu banco, tu correo), tu celular y el servidor hacen primero un **saludo secreto**: se ponen de acuerdo en una clave que solo ellos conocen, y con ella cifran todo lo demás. Ese saludo se llama *handshake*.

Hoy ese saludo usa matemáticas que una **computadora cuántica** del futuro podría romper. Lo preocupante es que un atacante puede **guardar hoy** el tráfico cifrado y **descifrarlo años después**, cuando tenga esa computadora. Por eso ya existe un saludo nuevo, llamado **híbrido postcuántico**, que combina el método de siempre con uno resistente a computadoras cuánticas.

La duda es que el saludo nuevo es **más pesado**: manda unas 3 veces más datos. Entonces:

> **¿Cuánto más lenta se vuelve la conexión con el saludo nuevo, sobre todo en redes malas como las móviles?**

Eso es lo que este proyecto mide.

---

## 2. ¿Qué construimos?

Construimos un **laboratorio automático dentro de la computadora**. En lugar de usar servidores y celulares reales, que serían caros y difíciles de controlar, creamos todo con **contenedores**: pequeñas "computadoras virtuales" que se encienden con un solo comando.

Una forma fácil de imaginarlo:

| Pieza del laboratorio | Analogía | Qué hace |
|---|---|---|
| **Servidor** (`pqc-server`) | Una tienda con dos puertas | La puerta 4433 atiende con el saludo **clásico**. La puerta 4434 atiende con el saludo **híbrido postcuántico**. |
| **Cliente** (`pqc-client-orchestrator`) | Un grupo de 100 clientes que llegan a la tienda a la vez | Simula a 100 personas conectándose al mismo tiempo y mide cuánto tarda cada saludo. |
| **Simulador de red** (`network_shaper.py`) | El "clima" de la calle entre los clientes y la tienda | Hace que la red sea lenta, que se pierdan paquetes o que los paquetes sean más pequeños, como en una red móvil mala o una VPN. |
| **Generador de carga** (`load_runner.py`) | El cronómetro | Lanza las 100 conexiones a la vez, cronometra cada una y anota si salió bien o falló. |
| **Graficador** (`analytics_plotter.py`) | El que arma el informe | Convierte las mediciones en gráficos y en una tabla lista para el artículo. |
| **Archivo de escenarios** (`scenarios_config.json`) | La receta del experimento | Dice qué tipos de red probar y con qué valores. Cambiando este archivo, el experimento se puede repetir con datos de otro país. |

Todo se ejecuta con **un solo comando** (`make bench`) y termina en unos 2 minutos.

---

## 3. ¿Qué probamos?

Probamos los **2 tipos de saludo** en **4 tipos de red**, es decir, **8 experimentos**:

| Tipo de red | Cómo es | Por qué la elegimos |
|---|---|---|
| Red perfecta | Sin retraso ni pérdidas | Para ver solo el costo del cálculo extra |
| 4G normal en Perú | 50 ms de retraso, 1 % de paquetes perdidos | Basada en los datos oficiales de OSIPTEL 2026 |
| Red móvil mala | 150 ms de retraso, 5 % de paquetes perdidos | Para ver qué pasa cuando la señal es pésima |
| Red con VPN | 50 ms de retraso, paquetes más pequeños (1280 bytes) | Las VPN achican los paquetes y el saludo pesado se tiene que partir en más pedazos |

En cada experimento se hacen **5 rondas de 100 conexiones simultáneas**: **500 saludos por experimento** y **4 000 en total**.

---

## 4. ¿Cómo nos aseguramos de que la medición sea justa?

Esta es la parte que da credibilidad al trabajo. Antes de medir, el laboratorio revisa que todo esté bien. Si algo falla, **se detiene y no guarda nada**, para no producir datos engañosos. A esto le llamamos **"fallar rápido"**.

Los controles más importantes, en palabras simples:

1. **Comprobar el "clima" de la red.** El simulador aplica el escenario y luego lo verifica. Si la red no quedó exactamente como se pidió, no se mide.
2. **Una sola forma de saludar por prueba.** Descubrimos que, por defecto, el cliente mandaba **las dos llaves** (la clásica y la nueva) en cada saludo. Eso hacía que lo que el cliente enviaba en el saludo "clásico" pesara 5 veces más de lo que debía (1 536 bytes en lugar de 302). Lo corregimos, y además el sistema lo verifica: intenta entrar por la puerta equivocada y **debe ser rechazado**. Si entra, la prueba se cancela.
3. **Siempre un saludo completo.** Los navegadores guardan un "pase" para saludar más rápido la próxima vez (*session ticket*). Si lo dejábamos activo, las mediciones saldrían artificialmente rápidas. Lo desactivamos en ambos lados, y el sistema comprueba que el servidor no entregue esos pases.
4. **Misma identificación en ambas puertas.** El servidor se identifica con el mismo certificado en las dos puertas. Así, la única diferencia entre ambas pruebas es el tipo de saludo.
5. **Calentamiento.** Antes de medir se hacen 10 conexiones de práctica, como calentar un motor.
6. **Límite de espera.** Si un saludo tarda más de 3 segundos, se cuenta como **fallido**, como cuando una página "se queda cargando".
7. **Mismas versiones en ambos lados.** Cliente y servidor usan la misma versión de la biblioteca de cifrado (OpenSSL 3.5.9), con el soporte postcuántico de Open Quantum Safe.

---

## 5. ¿Qué encontramos?

| Tipo de red | Saludo clásico | Saludo híbrido | Diferencia |
|---|---|---|---|
| Red perfecta | 39 ms | 64 ms | El híbrido tarda 25 ms más |
| 4G normal | 98 ms | 100 ms | Casi igual |
| Red móvil mala | 176 ms | 187 ms | Casi igual |
| Red con VPN | 102 ms | 108 ms | 6 ms más |

*ms = milésimas de segundo. Un parpadeo dura entre 100 y 400 ms.*

**En palabras simples:**

- **El saludo nuevo es unas 3 veces más pesado** (≈ 3 350 bytes frente a ≈ 1 090), pero **casi no se nota en el tiempo**.
- **Donde más se nota es en la red perfecta**, porque ahí no hay nada más que esperar y solo pesa el cálculo extra. Aun así son milésimas de segundo, imperceptibles para una persona.
- **En redes malas los dos métodos se comportan casi igual.** Lo que hace lenta la conexión es la mala señal, no el tipo de cifrado. Los fallos fueron muy pocos (0 a 3 de 500) y aparecieron en ambos métodos por igual.
- **Con VPN**, el saludo nuevo se parte en más pedazos (11 en vez de 8), pero eso solo le suma unas milésimas de segundo.

**Conclusión:** pasarse hoy al cifrado postcuántico **cuesta muy poco en velocidad**, incluso en las redes móviles de Perú. Y a cambio protege la información contra ataques futuros.

> **Ojo con un detalle honesto:** al repetir la prueba, los porcentajes cambian un poco (por ejemplo, en la red perfecta la diferencia salió 63 % una vez y 31 % otra). Eso pasa porque los 100 usuarios simulados y el servidor comparten la misma laptop y compiten por el procesador. Lo que **no cambia** es la conclusión general.

---

## 6. ¿Qué se entrega en este primer avance?

- ✅ El laboratorio completo, que se levanta con un comando.
- ✅ Los 8 experimentos funcionando de forma automática.
- ✅ Los controles de calidad (fallar rápido, una sola llave, sin pases de sesión).
- ✅ Los resultados guardados en archivos ordenados: datos crudos (`raw`), resúmenes (`manifests`) y gráficos (`figures`).
- ✅ Tres gráficos en calidad de publicación (300 dpi) y una tabla lista para el artículo.
- ✅ Un modo de **datos simulados** para practicar con los gráficos sin encender el laboratorio. Esos gráficos salen con la marca "DATOS SIMULADOS" para no confundirlos con los reales.
- ✅ 14 pruebas automáticas que revisan que el código haga lo que debe.

---

## 7. ¿Qué falta para los siguientes entregables?

- **Pruebas estadísticas.** La hipótesis habla de diferencias "significativas". Para afirmarlo con rigor falta aplicar pruebas que digan si la diferencia es real o casualidad.
- **Separar el procesador del cliente y del servidor** (darle a cada uno sus propios núcleos) y registrar el hardware usado, para que los números varíen menos entre repeticiones.
- **Repetir la campaña varias veces** y reportar cuánto varían los resultados.
- *(Opcional)* Un escenario de control con 50 ms, sin pérdidas y paquetes normales, para aislar mejor el efecto de la VPN.

---

## 8. Cómo mostrarlo en vivo (demo de 3 minutos)

1. **Encender el laboratorio:**

   ```bash
   make up
   ```

2. **Mostrar las dos puertas** (debe responder `X25519MLKEM768`, el saludo postcuántico):

   ```bash
   docker compose exec pqc-client-orchestrator sh -c 'printf "GET / HTTP/1.0\r\n\r\n" | openssl s_client -connect pqc-server:4434 -groups X25519MLKEM768 -ign_eof 2>&1 | grep -E "group|PQC-Bench"'
   ```

3. **Correr un experimento**, por ejemplo la red móvil mala con saludo híbrido. Se ven los controles de calidad y las 5 rondas de 100 conexiones:

   ```bash
   make exp EXP=EXP_06
   ```

4. **Mostrar los gráficos** en `output/figures/`.

---

## 9. Preguntas que te pueden hacer (y cómo responderlas)

**¿Por qué no usaron celulares y servidores reales?**
Porque en una red real no puedes controlar el clima: cada prueba saldría distinta. Con el laboratorio podemos poner exactamente "50 ms de retraso y 1 % de pérdida" y repetirlo cuantas veces queramos. Así trabajan también los estudios que citamos.

**¿Por qué 100 usuarios a la vez?**
Para simular un servidor con carga real, no una sola persona. Así se ve si el cálculo extra del saludo nuevo afecta cuando muchos llegan a la vez.

**¿Por qué 3 segundos de límite?**
Porque pasado ese tiempo una persona ya percibe la página como "colgada". Se cuenta como fallo de conexión.

**¿Los resultados confirman la hipótesis?**
La primera parte sí: en la red perfecta la diferencia existe, pero es pequeña en tiempo real. La segunda parte (que en redes malas el saludo nuevo falla mucho más) **no se observó** en nuestras pruebas: ambos se comportaron casi igual. Eso también es un resultado válido y útil, porque sugiere que migrar es viable. Falta confirmarlo con pruebas estadísticas.

**¿Se puede repetir en otro país?**
Sí. Basta con cambiar los valores de red en `scenarios_config.json`, por ejemplo con los datos del regulador de otro país, y volver a correr el comando.
