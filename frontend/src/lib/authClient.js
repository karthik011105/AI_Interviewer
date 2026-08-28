export const authClient = {
  getToken: () => localStorage.getItem("jwt_token"),
  setToken: (token) => localStorage.setItem("jwt_token", token),
  clearToken: () => localStorage.removeItem("jwt_token"),

  signup: async (email, password) => {
    const response = await fetch("/api/auth/signup", {
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
    const response = await fetch("/api/auth/login", {
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
      const response = await fetch("/api/auth/status", {
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
