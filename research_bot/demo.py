"""Clearly fictional fixtures for offline behavior and layout checks only."""
from .core import Article, rank


def demo_data():
    specs = [
        ("[架空] Platform delivery workers: heat exposure and health", "新しい働き方",
         "Fictional test abstract. A fictional cross-sectional survey examines heat exposure and self-reported symptoms among platform delivery workers. Associations do not establish causality. No numerical estimates are supplied.",
         "架空サンプル：配送プラットフォーム就業者の暑熱曝露と健康",
         "配送就業者の暑熱曝露と自覚症状の関連を検討するという架空の設定です。",
         "横断調査という架空の設定です。人数や効果量はありません。",
         "動作確認用のため、実際の研究結果はありません。",
         "実在する研究ではありません。研究上の根拠として引用できません。",
         "実際の情報を取得した際に、健康課題と新しい働き方を併せて表示するための見本です。"),
        ("[架空] Workplace mental health and long working hours", "メンタルヘルス",
         "Fictional test abstract. This fictional cohort study concerns working hours and mental health among workers. No results, sample size, or effect estimates are provided.",
         "架空サンプル：長時間労働と職場のメンタルヘルス",
         "労働時間とメンタルヘルスを扱う見本です。", "コホート研究という架空の設定です。",
         "実際の研究結果はありません。", "検証用データです。因果関係や効果量を示しません。",
         "過重労働とメンタルヘルスのタグ付けを確認するための項目です。"),
        ("[架空] Occupational chemical exposure and worker safety", "安全・職業性疾患",
         "Fictional test abstract concerning occupational chemical exposure and workplace safety. This is a synthetic fixture, not a publication.",
         "架空サンプル：化学物質曝露と職場の安全",
         "職業性の化学物質曝露を扱う見本です。", "実際の研究はありません。",
         "実際の研究結果はありません。", "架空の資料であり、予防指針ではありません。",
         "重点テーマ以外の安全衛生情報も選定する表示例です。"),
        ("[架空] 中小企業の健康経営と労働者の安全衛生に関する公式発表の見本", "中小企業・健康経営", "",
         "架空サンプル：中小企業の健康経営に関する発表",
         "抄録未取得：原文確認が必要", "抄録未取得：原文確認が必要",
         "抄録未取得：原文確認が必要", "架空の発表です。実際の政府方針や制度変更ではありません。",
         "公式発表と論文の表示を分けるための見本です。"),
    ]
    articles, summaries = [], []
    for i, spec in enumerate(specs, 1):
        title, tag, abstract, title_ja, purpose, methods, findings, limitations, implications = spec
        a = rank(Article.create(title=title, url=f"https://example.org/fictional-demo/{i}",
                 source="架空の動作確認データ", kind="政策・公式発表" if i == 4 else "学術論文",
                 published="架空・公表日なし", abstract=abstract, journal="架空サンプル（実在誌ではありません）"))
        articles.append(a)
        summaries.append(dict(id=a.id, title_ja=title_ja, purpose=purpose, methods=methods,
                              findings=findings, limitations=limitations, implications=implications))
    return articles, {"summaries": summaries, "research_ideas": [],
                      "editor_note": "全件が架空のサンプルです。ここでの日本語文章は固定の表示見本で、AI APIの動作確認ではありません。"}
