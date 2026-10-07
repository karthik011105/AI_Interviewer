// Resolved the same way as every page-level request (see App.jsx), so auth works
// in a production build too. A relative "/api/..." path would only resolve via the
// Vite dev-server proxy and would 404 once the frontend is served as static files.
const API_BASE_URL = import.meta.env?.VITE_API_BASE_URL || "http://127.0.0.1:8000";

/**
 * Turn a FastAPI error body into a sentence a person can act on.
 *
 * A 422 carries `detail` as a LIST of {loc, msg} objects, not a string. Passing
 * that straight to `new Error()` showed "[object Object]", so a too-short
 * password or a mistyped email looked like signup was simply broken.
 */
export function describeApiError(body, fallback) {
  const detail = body?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail) && detail.length) {
    return detail
      .map((item) => {
        const field = Array.isArray(item?.loc) ? item.loc[item.loc.length - 1] : "";
        const message = String(item?.msg || "").replace(/^Value error,\s*/i, "");
        if (field === "email") return `Email: ${message.replace(/^value is not a valid email address:\s*/i, "")}`;
        return message;
      })
      .filter(Boolean)
      .join(" ");
  }
  return fallback;
}

export const authClient = {
  getToken: () => localStorage.getItem("jwt_token"),
  setToken: (token) => localStorage.setItem("jwt_token", token),
  clearToken: () => localStorage.removeItem("jwt_token"),

  signup: async (email, password) => {
    const response = await fetch(`${API_BASE_URL}/auth/signup`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });

    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(describeApiError(err, "Signup failed."));
    }

    const data = await response.json();
    if (data.access_token) {
      authClient.setToken(data.access_token);
    }
    return data;
  },

  login: async (email, password) => {
    const response = await fetch(`${API_BASE_URL}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });

    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(describeApiError(err, "Login failed."));
    }

    const data = await response.json();
    if (data.access_token) {
      authClient.setToken(data.access_token);
    }
    return data;
  },

  forgotPassword: async (email) => {
    const response = await fetch(`${API_BASE_URL}/auth/forgot-password`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    });

    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(describeApiError(data, "Could not request a password reset."));
    }
    // The backend deliberately returns the same response whether or not the
    // address has an account — do not let a caller here branch on that.
    return data;
  },

  resetPassword: async (token, newPassword) => {
    const response = await fetch(`${API_BASE_URL}/auth/reset-password`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token, new_password: newPassword }),
    });

    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(describeApiError(data, "Could not reset the password."));
    }
    return data;
  },

  // Public sign-in options; google_client_id is null until it is configured.
  getAuthConfig: async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/auth/config`);
      if (!response.ok) return { google_client_id: null };
      return await response.json();
    } catch {
      return { google_client_id: null };
    }
  },

  // `credential` is the ID token Google Identity Services hands the page.
  googleSignIn: async (credential) => {
    const response = await fetch(`${API_BASE_URL}/auth/google`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ credential }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(describeApiError(data, "Google sign-in failed."));
    }
    if (data.access_token) {
      authClient.setToken(data.access_token);
    }
    return data;
  },

  logout: async () => {
    const token = authClient.getToken();
    // Tell the server to revoke this token, so it cannot be reused if it was
    // captured. Clearing it locally alone leaves it valid until it expires.
    if (token) {
      try {
        await fetch(`${API_BASE_URL}/auth/logout`, {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` },
        });
      } catch {
        // Network failure must not trap the user in a signed-in UI. The local
        // token is cleared regardless; it stays valid server-side until it
        // expires, which is the same exposure as before revocation existed.
      }
    }
    authClient.clearToken();
  },

  getSession: async () => {
    const token = authClient.getToken();
    if (!token) return { data: { session: null } };

    try {
      const response = await fetch(`${API_BASE_URL}/auth/status`, {
        headers: {
          Authorization: `Bearer ${token}`
        }
      });
      if (response.ok) {
        const data = await response.json();
        if (data.authenticated) {
          return { data: { session: { access_token: token, user: data.user } } };
        }
      }
      authClient.clearToken();
      return { data: { session: null } };
    } catch (e) {
      return { data: { session: null } };
    }
  }
};
