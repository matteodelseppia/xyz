const params = new URLSearchParams(window.location.search);
const tag = params.get("tag") ?? "";
const articles = [...document.querySelectorAll(".articles > li")];
const activeLink = document.querySelector(`[data-tag-link="${CSS.escape(tag)}"]`);

for (const article of articles) {
  article.hidden = Boolean(tag) && !article.dataset.tags?.split(" ").includes(tag);
}
for (const link of document.querySelectorAll("[data-tag-link]")) {
  if (link === activeLink) link.setAttribute("aria-current", "true");
}
if (tag) {
  const label = activeLink?.childNodes[0]?.textContent?.trim() ?? tag;
  const count = articles.filter(article => !article.hidden).length;
  const result = document.querySelector("[data-filter-result]");
  if (result) {
    result.textContent = `${count} ${count === 1 ? "article" : "articles"} tagged “${label}”.`;
    result.style.display = "block";
  }
}
