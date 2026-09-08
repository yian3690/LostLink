import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "LostLink AI 校園失物招領",
    short_name: "LostLink AI",
    description: "用照片與文字快速尋找校園遺失物",
    start_url: "/mobile",
    display: "standalone",
    background_color: "#f5f8f5",
    theme_color: "#08a75a",
    lang: "zh-Hant",
  };
}

