export const seo = (title: string, description: string) => ({
  meta: [
    { title: `${title} · PAL` },
    { name: "description", content: description },
    { property: "og:title", content: `${title} · PAL` },
    { property: "og:description", content: description },
    { property: "og:type", content: "website" },
    { name: "twitter:card", content: "summary" },
  ],
});
