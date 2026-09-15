/*
 * Paginación reutilizable del lado del cliente para tablas de listado.
 *
 * Se hace en el cliente (no con LIMIT/OFFSET en el servidor) a propósito:
 * varias de estas tablas ya tienen un buscador dinámico en cliente
 * (buscador.js, filtra filas ya cargadas en el DOM). Si el servidor
 * paginara, el buscador solo encontraría resultados dentro de la página
 * visible. Paginando en el cliente, todas las filas siguen cargadas y el
 * buscador sigue funcionando sobre el total; mientras hay una búsqueda
 * activa, se muestran todos los resultados sin paginar (son subconjuntos
 * chicos de todas formas).
 *
 * Uso: <table id="tabla-x" data-paginacion="10"> ... </table>
 * Si existe un <input data-buscador="tabla-x">, se coordina con él.
 */
document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-paginacion]").forEach(function (tabla) {
        const tamano = parseInt(tabla.getAttribute("data-paginacion"), 10) || 10;
        const cuerpo = tabla.querySelector("tbody");
        if (!cuerpo) return;

        const filas = Array.from(cuerpo.querySelectorAll("tr")).filter(
            (fila) => !fila.querySelector(".sin-resultados")
        );
        if (filas.length <= tamano) return; // no hace falta paginar

        const buscador = tabla.id ? document.querySelector('[data-buscador="' + tabla.id + '"]') : null;

        const contenedor = document.createElement("div");
        contenedor.className = "paginacion";
        tabla.insertAdjacentElement("afterend", contenedor);

        const totalPaginas = Math.ceil(filas.length / tamano);
        let paginaActual = 1;

        function estaBuscando() {
            return !!(buscador && buscador.value.trim() !== "");
        }

        function render() {
            if (estaBuscando()) {
                // buscador.js ya controla qué filas se muestran: no pisar
                // su filtro, solo ocultar los controles de paginación.
                contenedor.hidden = true;
                return;
            }

            contenedor.hidden = false;
            const inicio = (paginaActual - 1) * tamano;
            const fin = inicio + tamano;
            filas.forEach(function (fila, i) {
                fila.style.display = (i >= inicio && i < fin) ? "" : "none";
            });

            contenedor.innerHTML = "";

            const anterior = document.createElement("button");
            anterior.type = "button";
            anterior.className = "boton-secundario";
            anterior.textContent = "‹ Anterior";
            anterior.disabled = paginaActual === 1;
            anterior.addEventListener("click", function () {
                paginaActual -= 1;
                render();
            });

            const info = document.createElement("span");
            info.className = "paginacion-info";
            info.textContent = "Página " + paginaActual + " de " + totalPaginas;

            const siguiente = document.createElement("button");
            siguiente.type = "button";
            siguiente.className = "boton-secundario";
            siguiente.textContent = "Siguiente ›";
            siguiente.disabled = paginaActual === totalPaginas;
            siguiente.addEventListener("click", function () {
                paginaActual += 1;
                render();
            });

            contenedor.append(anterior, info, siguiente);
        }

        render();

        if (buscador) {
            buscador.addEventListener("input", function () {
                paginaActual = 1;
                render();
            });
        }
    });
});
