/* Authentication has its own state so pending requests cannot change mode. */
window.AIStoraAuth = (() => {
  class RequestError extends Error {
    constructor(message, status = 0, field = null, retryAfter = 0) {
      super(message); this.status = status; this.field = field; this.retryAfter = retryAfter;
    }
  }
  async function requestJSON(url, options = {}, timeoutMs = 20000) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(url, { ...options, signal: controller.signal });
      let data;
      try { data = await response.json(); } catch (_) {
        throw new RequestError(response.status >= 500
          ? "AIStora is temporarily unavailable. Please try again shortly."
          : "We couldn't read the server's response. Please try again.", response.status);
      }
      if (!data || typeof data !== "object" || Array.isArray(data)) {
        throw new RequestError("We couldn't read the server's response. Please try again.", response.status);
      }
      if (!response.ok || data.success === false) {
        const retryAfter = Number(response.headers.get("Retry-After")) || 0;
        const fallback = response.status === 429 ? "Too many attempts. Please wait and try again."
          : "AIStora is temporarily unavailable. Please try again shortly.";
        const message = response.status >= 500 ? fallback : data.error || fallback;
        throw new RequestError(message, response.status, data.field, retryAfter);
      }
      return data;
    } catch (error) {
      if (error instanceof RequestError) throw error;
      throw new RequestError(error.name === "AbortError"
        ? "This is taking longer than expected. Please try again."
        : "We couldn't reach AIStora. Check your connection and try again.");
    } finally { clearTimeout(timeout); }
  }
  function init(onSignedIn) {
    const el = id => document.getElementById(id);
    const form = el("auth-form"), email = el("auth-email"), password = el("auth-password");
    const submit = el("auth-submit-btn"), toggle = el("auth-toggle-mode"), reveal = el("auth-show-password");
    const notice = el("auth-error"), forgot = el("auth-forgot-password");
    let mode = "login", busy = false, recoveryAvailable = false;
    let resetToken = new URLSearchParams(location.hash.slice(1)).get("reset");
    // Sign-in and sign-up are separate pages: /signup opens in register mode.
    const path = location.pathname.replace(/\/+$/, "");
    const pageMode = path === "/signup" ? "register" : "login";
    const params = new URLSearchParams(location.search);
    const justRegistered = params.get("registered") === "1";
    // Reset tokens stay in the fragment: not in server access logs or referrers.
    if (resetToken) history.replaceState(null, "", location.pathname + location.search);
    else if (justRegistered) history.replaceState(null, "", location.pathname);
    function message(text, tone = "error", title = null, focus = true) {
      notice.dataset.tone = tone;
      el("auth-error-title").textContent = title || (tone === "success" ? "You're all set" : tone === "info" ? "Please note" : "There was a problem");
      el("auth-error-message").textContent = text;
      notice.classList.remove("hidden");
      if (focus) notice.focus();
    }
    function clearFields() {
      for (const name of ["email", "password"]) {
        el(`auth-${name}`).removeAttribute("aria-invalid");
        el(`auth-${name}-error`).textContent = "";
        el(`auth-${name}-error`).classList.add("hidden");
      }
    }
    function fieldError(name, text) {
      el(`auth-${name}`).setAttribute("aria-invalid", "true");
      el(`auth-${name}-error`).textContent = text;
      el(`auth-${name}-error`).classList.remove("hidden");
      el(`auth-${name}`).focus();
    }
    function render() {
      const labels = { login: "Sign in", register: "Create account", forgot: "Send reset link", reset: "Save new password" };
      const titles = { login: "Welcome back", register: "Start exploring", forgot: "Reset your password", reset: "Choose a new password" };
      const subtitles = { login: "Sign in to continue exploring your data.", register: "Create an account and bring your CSV files.", forgot: "Enter your email and we'll send a recovery link if an account exists.", reset: "Choose a passphrase you haven't used for this account before." };
      el("auth-title").textContent = titles[mode];
      el("auth-subtitle").textContent = subtitles[mode];
      el("auth-btn-label").textContent = busy ? ({login:"Signing in…", register:"Creating account…", forgot:"Sending link…", reset:"Saving password…"})[mode] : labels[mode];
      toggle.textContent = mode === "login" ? "New to AIStora? Create an account" : "Back to sign in";
      toggle.href = mode === "login" ? "/signup" : "/login";
      toggle.classList.toggle("is-disabled", busy);
      toggle.setAttribute("aria-disabled", String(busy));
      submit.disabled = forgot.disabled = busy;
      email.disabled = password.disabled = busy;
      form.setAttribute("aria-busy", String(busy));
      password.autocomplete = ["register", "reset"].includes(mode) ? "new-password" : "current-password";
      el("auth-password-hint").classList.toggle("hidden", !["register", "reset"].includes(mode));
      el("auth-password-group").classList.toggle("hidden", mode === "forgot");
      el("auth-email-group").classList.toggle("hidden", mode === "reset");
      forgot.classList.toggle("hidden", mode !== "login");
    }
    function setMode(next) {
      mode = next; password.value = ""; password.type = "password";
      reveal.textContent = "Show"; reveal.setAttribute("aria-label", "Show password"); reveal.setAttribute("aria-pressed", "false");
      notice.classList.add("hidden"); clearFields(); render();
    }
    // The toggle is a real link to /signup or /login; only block it mid-request.
    toggle.addEventListener("click", event => { if (busy) event.preventDefault(); });
    reveal.addEventListener("click", () => {
      const visible = password.type === "password";
      password.type = visible ? "text" : "password";
      reveal.textContent = visible ? "Hide" : "Show";
      reveal.setAttribute("aria-label", visible ? "Hide password" : "Show password");
      reveal.setAttribute("aria-pressed", String(visible));
    });
    forgot.addEventListener("click", () => {
      if (recoveryAvailable) setMode("forgot");
      else message("Password recovery isn't available on this installation yet. Please contact the site owner.", "info", "Recovery unavailable");
    });
    for (const name of ["email", "password"]) el(`auth-${name}`).addEventListener("input", () => {
      el(`auth-${name}`).removeAttribute("aria-invalid");
      el(`auth-${name}-error`).classList.add("hidden");
    });
    form.addEventListener("submit", async event => {
      event.preventDefault(); if (busy) return;
      clearFields(); notice.classList.add("hidden");
      email.value = email.value.trim();
      if (mode !== "reset" && (!email.value || !email.validity.valid || !/^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$/.test(email.value))) {
        fieldError("email", "Enter a valid email address."); return;
      }
      if (mode !== "forgot" && !password.value) { fieldError("password", "Enter your password."); return; }
      if (["register", "reset"].includes(mode) && (password.value.length < 10 || !password.value.trim())) {
        fieldError("password", "Use at least 10 characters, not only spaces."); return;
      }
      busy = true; render();
      const submittedMode = mode;
      try {
        const endpoints = {login:"/api/login", register:"/api/register", forgot:"/api/auth/forgot-password", reset:"/api/auth/reset-password"};
        const payload = mode === "forgot" ? {email:email.value} : mode === "reset" ? {token:resetToken,password:password.value} : {email:email.value,password:password.value};
        const data = await requestJSON(endpoints[mode], {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
        if (submittedMode === "login") { password.value = ""; await onSignedIn(data); }
        else if (submittedMode === "register") {
          // Hand the address to the sign-in page without putting it in the URL.
          try { sessionStorage.setItem("aistora:signup-email", email.value); } catch (_) {}
          location.assign("/login?registered=1"); return;
        }
        else if (submittedMode === "forgot") message("If that address has an account, we'll send a reset link. Check your inbox and spam folder.", "success", "Check your email");
        else { resetToken = null; setMode("login"); message("Your password has been updated. Sign in with your new password.", "success", "Password updated"); }
      } catch (error) {
        let text = error.message;
        if (error.status === 429 && error.retryAfter) text += ` Try again in ${Math.ceil(error.retryAfter / 60)} minute(s).`;
        if (error.field && ["email", "password"].includes(error.field)) fieldError(error.field, text);
        else message(text, "error", error.status === 401 ? "We couldn't sign you in" : "There was a problem");
      } finally { busy = false; render(); }
    });
    setMode(resetToken ? "reset" : pageMode);
    if (justRegistered && mode === "login") {
      try { email.value = sessionStorage.getItem("aistora:signup-email") || ""; sessionStorage.removeItem("aistora:signup-email"); } catch (_) {}
      message("Your account is ready. Sign in with your new password.", "success", "Account created", false);
      (email.value ? password : email).focus();
    }
    return {message, requestJSON, isReset:() => Boolean(resetToken), isAuthPage:() => path === "/login" || path === "/signup",
      setRecoveryAvailable: value => { recoveryAvailable = value === true; }};
  }
  return {init, requestJSON};
})();
