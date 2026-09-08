// Initialise lucide icons once the DOM is ready. Kept out of the templates
// because the Content-Security-Policy forbids inline scripts.
document.addEventListener("DOMContentLoaded", () => {
  if (window.lucide && typeof window.lucide.createIcons === "function") {
    window.lucide.createIcons();
  }
});
