import type { Metadata, Viewport } from "next";
import {
  ColorSchemeScript,
  MantineProvider,
  createTheme,
  mantineHtmlProps,
} from "@mantine/core";
import { config } from "@fortawesome/fontawesome-svg-core";
import "@mantine/core/styles.css";
import "@fortawesome/fontawesome-svg-core/styles.css";
import "./globals.css";
import "./styles/shell.css";
import "./styles/panels.css";
import "./styles/views.css";
import "./styles/responsive.css";

config.autoAddCss = false;

const theme = createTheme({
  primaryColor: "teal",
  defaultRadius: "md",
  fontFamily:
    'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  headings: {
    fontFamily:
      'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  },
});

export const metadata: Metadata = {
  title: "PiRouter · Network Console",
  description:
    "Local Raspberry Pi network, backup, service endpoint, and system health dashboard.",
  applicationName: "PiRouter",
  icons: { icon: "/favicon.svg" },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  viewportFit: "cover",
  themeColor: "#07100e",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" {...mantineHtmlProps}>
      <head>
        <ColorSchemeScript defaultColorScheme="dark" />
      </head>
      <body>
        <MantineProvider theme={theme} defaultColorScheme="dark">
          {children}
        </MantineProvider>
      </body>
    </html>
  );
}
