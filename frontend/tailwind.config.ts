import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: ["class"],
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}"
  ],
  theme: {
    extend: {
      colors: {
        background: "#07090f",
        card: "#0d1220",
        accent: "#8b5cf6",
        muted: "#93a4c2"
      },
      borderRadius: {
        xl: "1rem",
        "2xl": "1.25rem"
      },
      boxShadow: {
        glow: "0 0 40px rgba(139, 92, 246, 0.25)"
      }
    }
  },
  plugins: []
};

export default config;
