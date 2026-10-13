/*
 * Evita el doble envío de un formulario (doble clic en "Registrar venta", reintento por lentitud de la red).
 *
 * Uso:  <form data-una-vez> ... <button type="submit" data-texto-enviando="Registrando...">Registrar</button> </form>
 *
 * Al enviar, los botones de envío del formulario se desactivan y, si tienen data-texto-enviando, cambian su texto para
 * que el usuario vea que el sistema está trabajando. Es solo una ayuda visual: el servidor también rechaza el
 * mismo formulario si llega dos veces (código único por formulario), por si el navegador no ejecuta este archivo.
 */
(function () {
    "use strict";

    function bloquear(formulario) {
        formulario.dataset.enviando = "1";
        formulario.querySelectorAll('button[type="submit"], input[type="submit"]').forEach(function (boton) {
            if (!boton.dataset.textoOriginal) boton.dataset.textoOriginal = boton.textContent;
            if (boton.dataset.textoEnviando) boton.textContent = boton.dataset.textoEnviando;
            boton.disabled = true;
            boton.setAttribute("aria-busy", "true");
        });
    }

    function liberar(formulario) {
        delete formulario.dataset.enviando;
        formulario.querySelectorAll('button[type="submit"], input[type="submit"]').forEach(function (boton) {
            if (boton.dataset.textoOriginal) boton.textContent = boton.dataset.textoOriginal;
            boton.disabled = false;
            boton.removeAttribute("aria-busy");
        });
    }

    document.addEventListener("submit", function (evento) {
        var formulario = evento.target;
        if (!formulario.matches || !formulario.matches("form[data-una-vez]")) return;
        if (formulario.dataset.enviando) {
            evento.preventDefault(); // ya se envió: se ignora el segundo envío
            return;
        }
        // se desactivan los botones después de que el navegador arma el envío (si no, se perdería el envío)
        setTimeout(function () { bloquear(formulario); }, 0);
    });

    // al volver con el botón "atrás", el navegador puede restaurar la página con los botones desactivados
    window.addEventListener("pageshow", function (evento) {
        if (!evento.persisted) return;
        document.querySelectorAll("form[data-una-vez]").forEach(liberar);
    });
})();
