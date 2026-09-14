type ItemLike = {
  category: string | null;
  color: string | null;
  brand?: string | null;
  description?: string;
};

const CATEGORY_LABELS: Record<string, string> = {
  earphones: "耳機",
  phone: "手機",
  wallet: "錢包",
  keys: "鑰匙",
  card: "卡片或證件",
  umbrella: "雨傘",
  drink: "瓶裝飲料",
  bottle: "水壺或保溫瓶",
  bag: "包包",
  laptop: "筆記型電腦",
  mouse: "滑鼠",
  glasses: "眼鏡",
  charger: "充電器或線材",
  book: "書本或筆記本",
  clothing: "衣物",
  foam_roller: "按摩滾筒",
  toiletries: "盥洗或保養用品",
  stationery: "文具",
  watch: "手錶",
  jewelry: "飾品",
  shoes: "鞋子",
  helmet: "安全帽",
  ball: "球類",
  toy: "玩偶或玩具",
};

const COLOR_LABELS: Record<string, string> = {
  black: "黑色",
  white: "白色",
  blue: "藍色",
  red: "紅色",
  green: "綠色",
  gray: "灰色或銀色",
  silver: "銀色",
  pink: "粉紅色",
  yellow: "黃色",
  brown: "棕色",
  beige: "米色",
  orange: "橘色",
  purple: "紫色",
  transparent: "透明",
  multicolor: "多色",
};

const DESCRIPTION_NAMES: Array<[RegExp, string]> = [
  [/按摩滾筒|泡棉滾筒|按摩滾輪|瑜[珈伽]柱|滾筒/, "按摩滾筒"],
  [/保溫杯|保溫瓶/, "保溫瓶"],
  [/水壺|水瓶|水杯/, "水壺"],
  [/乳液/, "乳液"],
  [/雨傘|折疊傘|摺疊傘/, "雨傘"],
  [/耳機|耳塞/, "耳機"],
  [/錢包|皮夾|卡夾/, "錢包"],
  [/充電器|充電線|傳輸線/, "充電器或線材"],
];

function hasChinese(value: string): boolean {
  return /[\u3400-\u9fff]/.test(value);
}

export function categoryLabel(category: string | null, description = ""): string {
  const normalized = category?.trim().toLocaleLowerCase() ?? "";
  // A concrete noun in the description can repair legacy rows that were
  // previously stored as `other` or even assigned the wrong closed category.
  for (const [pattern, label] of DESCRIPTION_NAMES) {
    if (pattern.test(description)) return label;
  }
  if (CATEGORY_LABELS[normalized]) return CATEGORY_LABELS[normalized];
  if (category && hasChinese(category)) return category.trim();
  return "待補充物品名稱";
}

export function colorLabel(color: string | null): string {
  const normalized = color?.trim().toLocaleLowerCase() ?? "";
  if (!normalized) return "";
  if (COLOR_LABELS[normalized]) return COLOR_LABELS[normalized];
  return color && hasChinese(color) ? color.trim() : "";
}

export function itemDisplayName(item: ItemLike): string {
  const descriptiveName = `${colorLabel(item.color)}${categoryLabel(item.category, item.description)}`;
  const brand = item.brand?.trim();

  // Chinese modifiers and nouns do not need spaces (for example,
  // "黑色按摩滾筒"). Keep a brand visually separate because it may be Latin text.
  return brand ? `${brand} ${descriptiveName}` : descriptiveName;
}
