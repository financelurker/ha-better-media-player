// The panel's texts: English, German where Home Assistant runs in German.

const cache = {};

async function load(lang) {
  if (!cache[lang]) {
    cache[lang] = fetch(new URL(`./i18n/${lang}.json`, import.meta.url)).then((r) => (r.ok ? r.json() : {}));
  }
  return cache[lang];
}

// t(key, {name: value}) for the language; missing keys fall back to English
export async function translator(language) {
  const lang = String(language || "en").split("-")[0];
  const en = await load("en");
  const own = lang === "en" ? en : await load(lang).catch(() => ({}));
  return (key, vars = {}) => {
    const text = own[key] ?? en[key] ?? key;
    return text.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m));
  };
}
