<template>
    <svg
        width="46"
        height="46"
        viewBox="0 0 46 46"
        xmlns="http://www.w3.org/2000/svg"
        class="escr-logo"
        role="img"
        aria-labelledby="escr-logo-title"
    >
        <title id="escr-logo-title">Escript AI</title>
        <rect x="1" y="1" width="44" height="44" rx="9" class="escr-logo-mark" />
        <path d="M13 12H33V17H19V21H31V26H19V33H34V38H13V12Z" class="escr-logo-letter" />
        <path
            d="M26.6 9.8C31.8 8.4 36.2 9.7 39.1 13.8"
            class="escr-logo-flourish"
            stroke-width="2.3"
            stroke-linecap="round"
            fill="none"
        />
        <text x="25.5" y="36" class="escr-logo-ai">AI</text>
    </svg>
</template>
<script>
import "./EscrLogo.css";

export default {
    name: "EscrLogo",
}
</script>
