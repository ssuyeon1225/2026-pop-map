import json
import re

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st


# =========================================================
# 페이지 설정
# =========================================================
st.set_page_config(
    page_title="전국 고령화 단계구분도",
    page_icon="🗺️",
    layout="wide",
)

st.title("전국 고령화 단계구분도")
st.caption("2026년 6월 기준 · 시군구별 65세 이상 인구 비율")


# =========================================================
# 데이터 URL
# =========================================================
POPULATION_URL = (
    "https://raw.githubusercontent.com/greatsong/modudata/"
    "main/data/population_yearly.csv.gz"
)

GEOJSON_URL = (
    "https://raw.githubusercontent.com/greatsong/modudata/"
    "main/data/boundaries/sigungu_kr.geojson"
)


# =========================================================
# 코드 정리 함수
# =========================================================
def normalize_code(value, length=5):
    """
    행정구역 코드를 문자열로 변환한다.
    예:
        1111010100 -> "11110" (앞 5자리는 별도로 처리)
        11110.0    -> "11110"
    """
    if pd.isna(value):
        return None

    value = str(value).strip()

    # CSV에서 숫자로 읽혀 "11110.0"처럼 된 경우 대응
    if value.endswith(".0"):
        value = value[:-2]

    # 숫자만 남김
    value = re.sub(r"\D", "", value)

    if not value:
        return None

    return value.zfill(length)


# =========================================================
# 인구 데이터 불러오기
# =========================================================
@st.cache_data(show_spinner=False)
def load_population():
    df = pd.read_csv(
        POPULATION_URL,
        compression="gzip",
        dtype={"코드": str},
        low_memory=False,
    )

    # -----------------------------------------------------
    # 2026년만 선택
    # -----------------------------------------------------
    year_numeric = pd.to_numeric(df["연도"], errors="coerce")

    df = df.loc[year_numeric == 2026].copy()

    if df.empty:
        raise ValueError("2026년 인구 데이터를 찾을 수 없습니다.")

    # -----------------------------------------------------
    # 읍면동 코드 → 시군구 코드
    # 앞 5자리 사용
    # -----------------------------------------------------
    df["코드"] = (
        df["코드"]
        .astype(str)
        .str.replace(r"\.0$", "", regex=True)
        .str.strip()
    )

    df["시군구코드"] = df["코드"].str[:5]

    # -----------------------------------------------------
    # 전체 인구 열
    # 남_, 여_ 열은 제외
    # -----------------------------------------------------
    total_columns = [
        col
        for col in df.columns
        if str(col).startswith("계_")
    ]

    if not total_columns:
        raise ValueError("'계_'로 시작하는 인구 열을 찾을 수 없습니다.")

    # -----------------------------------------------------
    # 65세 이상 인구 열
    # 계_65세 ~ 계_100세 이상
    # -----------------------------------------------------
    elderly_columns = []

    for col in total_columns:
        match = re.match(r"^계_(\d+)세(?:\s*이상)?$", str(col))

        if match:
            age = int(match.group(1))

            if age >= 65:
                elderly_columns.append(col)

    if not elderly_columns:
        raise ValueError("65세 이상 인구 열을 찾을 수 없습니다.")

    # -----------------------------------------------------
    # 숫자로 변환
    # 쉼표 등이 들어 있는 경우도 처리
    # -----------------------------------------------------
    population_columns = list(
        set(total_columns + elderly_columns)
    )

    for col in population_columns:
        df[col] = (
            df[col]
            .astype(str)
            .str.replace(",", "", regex=False)
        )

        df[col] = pd.to_numeric(
            df[col],
            errors="coerce",
        ).fillna(0)

    # -----------------------------------------------------
    # 읍면동별 전체 / 고령인구 계산
    # -----------------------------------------------------
    df["전체인구"] = df[total_columns].sum(axis=1)

    df["65세이상"] = df[elderly_columns].sum(axis=1)

    # -----------------------------------------------------
    # 시군구별 합계
    # -----------------------------------------------------
    sigungu = (
        df.groupby("시군구코드", as_index=False)
        .agg(
            전체인구=("전체인구", "sum"),
            고령인구=("65세이상", "sum"),
        )
    )

    # -----------------------------------------------------
    # 고령화율 계산
    # -----------------------------------------------------
    sigungu["고령화율"] = (
        sigungu["고령인구"]
        / sigungu["전체인구"]
        * 100
    )

    sigungu.loc[
        sigungu["전체인구"] == 0,
        "고령화율"
    ] = pd.NA

    sigungu["시군구코드"] = (
        sigungu["시군구코드"]
        .astype(str)
        .str.zfill(5)
    )

    return sigungu


# =========================================================
# GeoJSON 불러오기
# =========================================================
@st.cache_data(show_spinner=False)
def load_geojson():
    response = requests.get(
        GEOJSON_URL,
        timeout=30,
    )

    response.raise_for_status()

    geojson = response.json()

    # -----------------------------------------------------
    # GeoJSON의 코드도 반드시 문자열로 통일
    # -----------------------------------------------------
    for feature in geojson["features"]:
        properties = feature.get("properties", {})

        code = properties.get("코드")

        code = normalize_code(code, length=5)

        properties["코드"] = code

    return geojson


# =========================================================
# 데이터 준비
# =========================================================
try:
    with st.spinner("2026년 인구 데이터와 행정구역 경계를 불러오는 중입니다..."):
        population = load_population()
        geojson = load_geojson()

except Exception as e:
    st.error(f"데이터를 불러오는 중 오류가 발생했습니다.\n\n{e}")
    st.stop()


# =========================================================
# GeoJSON 속성 → 데이터프레임
# =========================================================
geo_records = []

for feature in geojson["features"]:

    properties = feature.get(
        "properties",
        {},
    )

    geo_records.append(
        {
            "코드": properties.get("코드"),
            "시도": properties.get("시도"),
            "시군구": properties.get("시군구"),
        }
    )


geo_df = pd.DataFrame(geo_records)

geo_df["코드"] = (
    geo_df["코드"]
    .astype(str)
    .str.zfill(5)
)


# =========================================================
# 인구 데이터 + 경계 속성 결합
# 코드 기준으로만 매칭
# =========================================================
map_df = geo_df.merge(
    population,
    left_on="코드",
    right_on="시군구코드",
    how="left",
)


# =========================================================
# 상단 지표
# =========================================================
valid_df = map_df.dropna(
    subset=["고령화율"]
).copy()


col1, col2, col3 = st.columns(3)

with col1:
    st.metric(
        "시군구 수",
        f"{len(valid_df):,}개",
    )

with col2:
    national_rate = (
        valid_df["고령인구"].sum()
        / valid_df["전체인구"].sum()
        * 100
    )

    st.metric(
        "전국 65세 이상 비율",
        f"{national_rate:.1f}%",
    )

with col3:
    if not valid_df.empty:
        highest = valid_df.loc[
            valid_df["고령화율"].idxmax()
        ]

        st.metric(
            "고령화율 최고 지역",
            f"{highest['시군구']} {highest['고령화율']:.1f}%",
        )


st.divider()


# =========================================================
# Plotly 단계구분도
# =========================================================
if valid_df.empty:
    st.warning(
        "GeoJSON 코드와 인구 데이터 코드가 일치하지 않습니다."
    )
    st.stop()


# hover에 표시할 내용
customdata = valid_df[
    [
        "시도",
        "시군구",
        "전체인구",
        "고령인구",
    ]
].copy()


fig = go.Figure(
    go.Choropleth(
        geojson=geojson,

        # GeoJSON 안의 코드 위치
        featureidkey="properties.코드",

        # 데이터의 시군구 코드
        locations=valid_df["코드"],

        # 색상 값
        z=valid_df["고령화율"],

        # 고령화율이 높을수록 진한 색
        colorscale="Reds",

        # 경계선
        marker_line_color="white",
        marker_line_width=0.7,

        # hover 추가 데이터
        customdata=customdata,

        hovertemplate=(
            "<b>%{customdata[1]}</b>"
            "<br>"
            "%{customdata[0]}"
            "<br><br>"
            "65세 이상 비율: <b>%{z:.1f}%</b>"
            "<br>"
            "전체 인구: %{customdata[2]:,.0f}명"
            "<br>"
            "65세 이상: %{customdata[3]:,.0f}명"
            "<extra></extra>"
        ),

        colorbar=dict(
            title="65세 이상<br>인구 비율 (%)",
            thickness=15,
            len=0.75,
        ),
    )
)


# =========================================================
# 지도 레이아웃
# 배경지도 없이 GeoJSON 경계만 표시
# =========================================================
fig.update_geos(
    fitbounds="locations",
    visible=False,

    # 대한민국 지도에 적합한 투영
    projection_type="mercator",
)


fig.update_layout(
    title=dict(
        text="2026년 전국 시군구별 고령화율",
        x=0.5,
        xanchor="center",
    ),

    margin=dict(
        l=0,
        r=0,
        t=60,
        b=0,
    ),

    height=850,

    paper_bgcolor="white",
    plot_bgcolor="white",
)


st.plotly_chart(
    fig,
    use_container_width=True,
)


# =========================================================
# 설명
# =========================================================
st.caption(
    "고령화율 = 시군구 내 65세 이상 인구 ÷ 전체 인구 × 100. "
    "읍·면·동 행정구역 코드의 앞 5자리와 시군구 GeoJSON의 '코드'를 "
    "문자열 기준으로 매칭했습니다."
)


# =========================================================
# 데이터 확인용
# =========================================================
with st.expander("시군구별 고령화율 데이터 보기"):

    display_df = (
        valid_df[
            [
                "시도",
                "시군구",
                "코드",
                "전체인구",
                "고령인구",
                "고령화율",
            ]
        ]
        .sort_values(
            "고령화율",
            ascending=False,
        )
        .reset_index(drop=True)
    )

    display_df["고령화율"] = display_df[
        "고령화율"
    ].round(2)

    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
    )


# =========================================================
# 코드 매칭 확인
# =========================================================
with st.expander("데이터 코드 매칭 상태 확인"):

    population_codes = set(
        population["시군구코드"].dropna()
    )

    geo_codes = set(
        geo_df["코드"].dropna()
    )

    matched = population_codes & geo_codes

    only_population = population_codes - geo_codes
    only_geo = geo_codes - population_codes

    c1, c2, c3 = st.columns(3)

    c1.metric(
        "매칭 코드",
        len(matched),
    )

    c2.metric(
        "인구 데이터에만 존재",
        len(only_population),
    )

    c3.metric(
        "경계 데이터에만 존재",
        len(only_geo),
    )

    if only_population:
        st.write(
            "**경계 파일과 매칭되지 않은 인구 데이터 코드**"
        )

        st.write(
            sorted(only_population)
        )

    if only_geo:
        st.write(
            "**인구 데이터와 매칭되지 않은 GeoJSON 코드**"
        )

        st.write(
            sorted(only_geo)
        )
