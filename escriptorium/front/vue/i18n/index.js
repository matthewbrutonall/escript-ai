import Vue from "vue";
import VueI18n from "vue-i18n";
import Cookies from "js-cookie";
import messages from "./messages";

Vue.use(VueI18n);

export const SUPPORTED = ["en", "ar", "hi", "pl", "it", "es", "pt"];

export function detectLocale() {
    const fromHtml = (document.documentElement.getAttribute("lang") || "").split("-")[0];
    const fromCookie = (Cookies.get("django_language") || "").split("-")[0];
    for (const code of [fromHtml, fromCookie]) {
        if (SUPPORTED.includes(code)) return code;
    }
    return "en";
}

export function applyDir(locale) {
    const html = document.documentElement;
    html.setAttribute("lang", locale);
    html.setAttribute("dir", locale === "ar" ? "rtl" : "ltr");
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
    if (!SUPPORTED.includes(code)) return;
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
