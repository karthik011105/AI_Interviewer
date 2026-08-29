// Resolved the same way as every page-level request (see App.jsx), so auth works
// in a production build too. A relative "/api/..." path would only resolve via the
// Vite dev-server proxy and would 404 once the frontend is served as static files.
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

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
      const err = await response.json();
      throw new Error(err.detail || "Signup failed");
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
      const err = await response.json();
      throw new Error(err.detail || "Login failed");
    }

    const data = await response.json();
    if (data.access_token) {
      authClient.setToken(data.access_token);
    }
    return data;
  },

  logout: () => {
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
