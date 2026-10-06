/*
 * Modo privacidad: oculta los montos de dinero en pantalla.
 *
 * Sirve para presentaciones y para quien usa el sistema con otras personas cerca
 * de su pantalla. Todo lo marcado con la clase "monto" se difumina (ver style.css).
 *
 *  - Botón "Ocultar montos" en la barra superior (solo en pantallas con montos), o atajo de teclado Alt + P.
 *  - Se recuerda en este navegador (localStorage) hasta que se desactive.
 *  - Se aplica ANTES de que se dibuje la página, para que los montos nunca se vean
 *    un instante al cambiar de pantalla.
 *
 * Alcance: es privacidad VISUAL (que nadie lea los montos mirando la pantalla).
 * No cambia los datos que viajan al navegador, ni las descargas CSV/Excel.
 */
(function () {
    var CLAVE = "agrofalma_privacidad";
    var raiz = document.documentElement;

    function leer() {
        try { return window.localStorage.getItem(CLAVE) === "1"; } catch (e) { return false; }
    }
    function guardar(activo) {
        try { window.localStorage.setItem(CLAVE, activo ? "1" : "0"); } catch (e) { /* sin almacenamiento: queda solo en esta página */ }
    }

    var hayMontos = false;
    var activo = leer();
    if (activo) raiz.classList.add("privado");

    function pintarBoton() {
        var boton = document.getElementById("boton-privacidad");
        if (!boton) return;
        boton.setAttribute("aria-pressed", activo ? "true" : "false");
        var texto = boton.querySelector(".privacidad-texto");
        if (texto) texto.textContent = activo ? "Montos ocultos" : "Ocultar montos";
    }

    function alternar() {
        activo = !activo;
        raiz.classList.toggle("privado", activo);
        guardar(activo);
        pintarBoton();
        document.dispatchEvent(new CustomEvent("privacidad-cambio", { detail: { activo: activo } }));
    }

    document.addEventListener("DOMContentLoaded", function () {
        var boton = document.getElementById("boton-privacidad");
        // el botón solo aparece en pantallas que muestran montos de dinero
        hayMontos = !!document.querySelector(".monto");
        if (boton && hayMontos) {
            boton.hidden = false;
            boton.addEventListener("click", alternar);
        }
        pintarBoton();
    });

    document.addEventListener("keydown", function (evento) {
        if (hayMontos && evento.altKey && !evento.ctrlKey && !evento.metaKey && (evento.key === "p" || evento.key === "P")) {
            evento.preventDefault();
            alternar();
        }
    });
})();
