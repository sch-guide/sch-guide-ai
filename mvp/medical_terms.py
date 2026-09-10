"""병원 지침 검색에 사용하는 검토된 의료 약어·동의어 사전."""

# 일반 단어를 무리하게 확장하면 다른 시술 지침이 섞일 수 있습니다.
# 실제 병원 용례를 확인한 표현만 이 파일에 추가합니다.
ABBREVIATIONS = ("pcn", "foley", "cpe", "cre", "vre", "picc", "vancomycin")

ALIASES = {
    "pcn": (
        "pcn", "pcn irrigation", "percutaneous nephrostomy",
        "nephrostomy catheter", "경피적 신루", "경피 신루", "신루관", "신루관 세척",
    ),
    "foley": ("foley", "foley catheter", "폴리", "폴리 카테터", "유치도뇨관"),
    "picc": (
        "picc", "peripherally inserted central catheter",
        "말초삽입 중심정맥관", "말초삽입중심정맥관",
    ),
    "vre": (
        "vre", "vancomycin resistant enterococci", "vancomycin-resistant enterococci",
        "반코마이신 내성 장알균", "반코마이신내성장알균",
    ),
    "cre": (
        "cre", "carbapenem resistant enterobacterales", "carbapenem-resistant enterobacterales",
        "카바페넴 내성 장내세균", "카바페넴내성장내세균",
    ),
    "cpe": (
        "cpe", "carbapenemase producing enterobacterales", "carbapenemase-producing enterobacterales",
        "카바페넴분해효소 생성 장내세균", "카바페넴분해효소생성장내세균",
    ),
    "vancomycin": ("vancomycin", "vanco", "반코마이신", "반코마이신 투여"),
    "thoracentesis": ("thoracentesis", "흉강천자", "흉막천자", "늑막천자"),
}
