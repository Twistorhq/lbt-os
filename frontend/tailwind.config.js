/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        brand: {
          50:  '#eff6ff',
          100: '#dbeafe',
          200: '#bfdbfe',
          300: '#93c5fd',
          400: '#60a5fa',
          500: '#3b82f6',
          600: '#2563eb',
          700: '#1d4ed8',
          900: '#1e3a5f',
        },
        // TW-159: Twistor Trades marketing palette. Food-named per Sofia's
        // convention — sofrito (deep night-navy base), gold (amber accent).
        sofrito: {
          700: '#20204a',
          900: '#101026',
          950: '#0a0a18',
        },
        gold: {
          300: '#f8cd7a',
          400: '#f5b942',
          500: '#eba937',
          600: '#d18f1d',
        },
        cream: '#faf7f1',
        ink: {
          950: '#09111f',
        },
      },
      fontFamily: {
        sans: ['"Space Grotesk"', 'Inter', 'system-ui', 'sans-serif'],
      },
      keyframes: {
        floaty: {
          '0%, 100%': { transform: 'translateY(0px)' },
          '50%': { transform: 'translateY(-6px)' },
        },
        glowPulse: {
          '0%, 100%': { opacity: '0.7', transform: 'scale(1)' },
          '50%': { opacity: '1', transform: 'scale(1.03)' },
        },
        shine: {
          '0%': { transform: 'translateX(-140%) skewX(-18deg)' },
          '100%': { transform: 'translateX(220%) skewX(-18deg)' },
        },
        riseIn: {
          '0%': { opacity: '0', transform: 'translateY(14px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        marquee: {
          '0%': { transform: 'translateX(0)' },
          '100%': { transform: 'translateX(-50%)' },
        },
      },
      animation: {
        floaty: 'floaty 6s ease-in-out infinite',
        glowPulse: 'glowPulse 3.5s ease-in-out infinite',
        shine: 'shine 1.2s ease',
        riseIn: 'riseIn 500ms cubic-bezier(0.16,1,0.3,1)',
        marquee: 'marquee 38s linear infinite',
      },
    },
  },
  plugins: [],
}
