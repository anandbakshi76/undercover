// jsdom has no matchMedia, which the theme reads.
window.matchMedia = (query: string): MediaQueryList =>
  ({
    matches: false,
    media: query,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  }) as unknown as MediaQueryList;
