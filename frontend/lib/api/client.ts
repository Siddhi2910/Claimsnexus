import axios from "axios";

const apiRoot = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const baseURL = `${apiRoot.replace(/\/$/, "")}/api/v1/`;

export const api = axios.create({
  baseURL,
  timeout: 120000,
  headers: {
    "Content-Type": "application/json"
  }
});

api.interceptors.request.use((config) => {
  if (typeof window !== "undefined") {
    const token = localStorage.getItem("claimsnexus_token");
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
  }
  return config;
});
