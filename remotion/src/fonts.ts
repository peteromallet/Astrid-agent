import {
  cancelRender,
  continueRender,
  delayRender,
  staticFile,
} from "remotion";
import { createElement, useEffect, useState, type ReactElement } from "react";

export type LocalFontFace = {
  family: string;
  file: string;
  weight: number;
  /** Omitted means upright ("normal"). */
  style?: "italic";
};

/**
 * Every font named by the 2RP theme and the Astrid video brand is shipped
 * locally. Keep this list in lockstep with public/fonts/FONT_PROVENANCE.md and
 * never add a hosted source.
 */
export const LOCAL_FONT_FACES: readonly LocalFontFace[] = [
  { family: "Sixtyfour", file: "fonts/Sixtyfour.woff2", weight: 400 },
  { family: "Inter", file: "fonts/Inter-Regular.woff2", weight: 400 },
  { family: "Inter", file: "fonts/Inter-Bold.woff2", weight: 700 },
  {
    family: "JetBrains Mono",
    file: "fonts/JetBrainsMono-Regular.woff2",
    weight: 400,
  },
  {
    family: "JetBrains Mono",
    file: "fonts/JetBrainsMono-Bold.woff2",
    weight: 700,
  },
  {
    family: "Departure Mono",
    file: "fonts/DepartureMono-Regular.woff2",
    weight: 400,
  },
  { family: "Gelasio", file: "fonts/Gelasio-Regular.woff2", weight: 400 },
  {
    family: "Gelasio",
    file: "fonts/Gelasio-Italic.woff2",
    weight: 400,
    style: "italic",
  },
  { family: "Gelasio", file: "fonts/Gelasio-Bold.woff2", weight: 700 },
  {
    family: "Gelasio",
    file: "fonts/Gelasio-BoldItalic.woff2",
    weight: 700,
    style: "italic",
  },
];

// A representative probe makes the browser load the actual glyph path used
// by the render instead of merely resolving a family name.
export const LOCAL_FONT_GLYPH_PROBE = "Astrid 2RP — Aa 0123";

const fontSpec = ({ family, weight, style }: LocalFontFace): string =>
  `${style === "italic" ? "italic " : ""}${weight} 16px "${family}"`;

const faceLabel = ({ family, weight, style }: LocalFontFace): string =>
  `${family}/${style ?? "normal"}/${weight}`;

export const getLocalFontFaceCss = (): string =>
  LOCAL_FONT_FACES.map(
    ({ family, file, weight, style = "normal" }) =>
      `@font-face { font-family: '${family}'; src: url('${staticFile(file)}') format('woff2'); font-weight: ${weight}; font-style: ${style}; font-display: block; }`,
  ).join("\n");

/**
 * Inject local @font-face declarations and hold Remotion until each shipped
 * face has been loaded. A failed local load cancels the render rather than
 * silently rendering with a system fallback.
 */
export const FontProvider = (): ReactElement => {
  const [renderHandle] = useState(() =>
    delayRender("Loading Astrid local fonts"),
  );

  useEffect(() => {
    let cancelled = false;
    const loadFonts = async (): Promise<void> => {
      try {
        if (typeof document === "undefined" || !document.fonts) {
          throw new Error(
            "Astrid local fonts require the browser FontFaceSet API",
          );
        }

        await Promise.all(
          LOCAL_FONT_FACES.map((face) =>
            document.fonts.load(fontSpec(face), LOCAL_FONT_GLYPH_PROBE),
          ),
        );

        const missing = LOCAL_FONT_FACES.filter(
          (face) =>
            !document.fonts.check(fontSpec(face), LOCAL_FONT_GLYPH_PROBE),
        );
        if (missing.length > 0) {
          throw new Error(
            `Astrid local font load incomplete: ${missing.map(faceLabel).join(", ")}`,
          );
        }

        if (!cancelled) {
          continueRender(renderHandle);
        }
      } catch (error) {
        if (!cancelled) {
          cancelRender(
            error instanceof Error ? error : new Error(String(error)),
          );
        }
      }
    };

    void loadFonts();
    return () => {
      cancelled = true;
    };
  }, [renderHandle]);

  return createElement(
    "style",
    { "data-astrid-local-fonts": true },
    getLocalFontFaceCss(),
  );
};
