const registerForm = document.querySelector("#registerForm");
const loginForm = document.querySelector("#loginForm");
const authStatus = document.querySelector("#authStatus");

function setAuthStatus(message, isError = false) {
  authStatus.textContent = message;
  authStatus.classList.toggle("error-text", isError);
}

async function authRequest(formElement, endpoint) {
  const payload = new FormData(formElement);
  const response = await fetch(endpoint, {
    method: "POST",
    body: payload,
    credentials: "same-origin",
  });
  const result = await response.json();
  if (!response.ok) {
    throw new Error(result.detail || "Authentication failed.");
  }
  window.location.href = "/";
}

async function checkSession() {
  const response = await fetch("/api/auth/me", { credentials: "same-origin" });
  const result = await response.json();
  if (result.user) {
    window.location.href = "/";
  }
}

registerForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  setAuthStatus("Creating account.");
  try {
    await authRequest(registerForm, "/api/auth/register");
  } catch (error) {
    console.error(error);
    setAuthStatus(error.message || "Registration failed.", true);
  }
});

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  setAuthStatus("Signing in.");
  try {
    await authRequest(loginForm, "/api/auth/login");
  } catch (error) {
    console.error(error);
    setAuthStatus(error.message || "Login failed.", true);
  }
});

checkSession().catch((error) => {
  console.error(error);
});
