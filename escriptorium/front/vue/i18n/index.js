import Vue from "vue";
import VueI18n from "vue-i18n";
import Cookies from "js-cookie";
import messages from "./messages";
import { supportedLocales, readUiLanguages } from "./languages";

Vue.use(VueI18n);

export { readUiLanguages, supportedLocales };

export function detectLocale() {
    const allowed = supportedLocales();
    const fromHtml = (document.documentElement.getAttribute("lang") || "").split("-")[0];
    const fromCookie = (Cookies.get("django_language") || "").split("-")[0];
    for (const code of [fromHtml, fromCookie]) {
        if (allowed.includes(code)) return code;
    }
    return allowed[0] || "en";
}

export function applyDir(locale) {
    const html = document.documentElement;
    if (!html.getAttribute("lang")) {
        html.setAttribute("lang", locale);
    }
    // Django already set dir from LANGUAGE_BIDI; do not override.
}

const locale = detectLocale();
applyDir(locale);

const i18n = new VueI18n({
    locale,
    fallbackLocale: "en",
    messages,
    silentTranslationWarn: true,
});

export async function setLanguage(code) {
    if (!supportedLocales().includes(code)) return;
    const body = new URLSearchParams();
    body.set("language", code);
    body.set("next", window.location.pathname || "/");
    const csrf = Cookies.get("csrftoken");
    if (csrf) body.set("csrfmiddlewaretoken", csrf);
    await fetch("/i18n/setlang/", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body,
    });
    window.location.reload();
}

export default i18n;
