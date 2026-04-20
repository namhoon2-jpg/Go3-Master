import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import pdfplumber
import requests
import re
import io
import google.generativeai as genai

# ==========================================
# 1. 보안 및 API 설정
# ==========================================
try:
    API_KEY = st.secrets["GEMINI_API_KEY"]
    GSHEET_SCRIPT_URL = st.secrets["GSHEET_SCRIPT_URL"]
    genai.configure(api_key=API_KEY)
    model = genai.GenerativeModel("gemini-2.5-flash") 
except Exception as e:
    st.error(f"⚠️ 시스템 설정 오류: {e}")

if "analysis_result" not in st.session_state: st.session_state.analysis_result = ""
if "chat_history" not in st.session_state: st.session_state.chat_history = []

# ==========================================
# 2. [초정밀] 인쇄 및 레이아웃 CSS
# ==========================================
st.markdown("""
    <style>
    /* 기본 화면 스타일 */
    .stApp { background-color: #ffffff; }
    
    @media print {
        /* 1. 스트림릿 UI 요소 제거 */
        [data-testid="stSidebar"], header, footer, .stChatInput, .no-print, [data-baseweb="tab-list"], .stActionButton {
            display: none !important;
        }

        /* 2. [핵심] 모든 부모 컨테이너의 높이 및 스크롤 제한 해제 */
        html, body, #root, .stApp, 
        [data-testid="stAppViewContainer"], 
        [data-testid="stMainBlockContainer"],
        [data-testid="stVerticalBlock"],
        [data-testid="stVerticalBlockBorderWrapper"],
        .main {
            display: block !important;
            height: auto !important;
            min-height: auto !important;
            max-height: none !important;
            overflow: visible !important;
            position: static !important;
        }
        
        .block-container {
            max-width: 100% !important;
            padding: 0 !important;
            margin: 0 !important;
            overflow: visible !important;
        }

        /* 3. [그래프 보호] Plotly 내부 overflow는 건드리지 않음 (증발 방지) */
        .js-plotly-plot .plotly .main-svg {
            overflow: visible !important;
        }

        /* 4. 레이아웃 1단 정렬 */
        [data-testid="stHorizontalBlock"] {
            display: block !important;
        }
        [data-testid="column"] {
            width: 100% !important;
            max-width: 100% !important;
            margin-bottom: 50px !important;
            page-break-inside: avoid !important;
        }

        /* 5. 텍스트 가독성 */
        h2 { text-align: center; border-bottom: 3px solid black; padding-bottom: 10px; }
        h3 { border-left: 10px solid #1a73e8; padding-left: 15px; margin-top: 40px; page-break-after: avoid; }
        p, li { font-size: 12pt !important; line-height: 1.8; color: #000; }
        
        @page { size: A4; margin: 2cm 1.5cm; }
    }
    li { margin-bottom: 10px; }
    </style>
    """, unsafe_allow_html=True)

# ==========================================
# 3. 데이터 가공 함수
# ==========================================
def sync_knowledge(new_content=None):
    try:
        if new_content: requests.post(GSHEET_SCRIPT_URL, json={"content": new_content})
        response = requests.get(GSHEET_SCRIPT_URL)
        return response.text if response.status_code == 200 else ""
    except: return ""

@st.cache_data(show_spinner=False)
def process_performance_data(file_bytes):
    xls = pd.ExcelFile(io.BytesIO(file_bytes))
    i_df, m_df = pd.DataFrame(), pd.DataFrame()
    def safe_grade(val):
        try:
            if pd.isna(val): return None
            m = re.search(r'([1-9])', str(val).strip())
            return float(m.group(1)) if m else None
        except: return None
    if '학생부현황' in xls.sheet_names:
        df = pd.read_excel(xls, sheet_name='학생부현황')
        res = []
        for g in [1.0, 2.0, 3.0]:
            for s_idx, (u_c, r_c) in enumerate([(3, 5), (10, 12)], 1):
                sub = df[df.iloc[:, 0] == g]
                u_s, w_s = 0, 0
                for _, row in sub.iterrows():
                    try:
                        u, r = float(row.iloc[u_c]), str(row.iloc[r_c]).strip()
                        m = re.search(r'^[1-9]', r)
                        if m: u_s += u; w_s += (u * float(m.group()))
                    except: continue
                if u_s > 0: res.append({"학기": f"{int(g)}-{s_idx}", "등급": round(w_s/u_s, 2)})
        i_df = pd.DataFrame(res)
    if '수능모의고사' in xls.sheet_names:
        df_m = pd.read_excel(xls, sheet_name='수능모의고사')
        m_res = []
        for _, row in df_m.iterrows():
            try:
                txt = str(row.iloc[0]); g_m = re.search(r'(\d)학년', txt); d_m = re.search(r'\((\d{2})-(\d{2})\)', txt)
                if g_m and d_m:
                    m_res.append({
                        "key": int(f"{d_m.group(1)}{d_m.group(2)}"), "시험": f"{g_m.group(1)}학년 {d_m.group(2)}월", 
                        "국어": safe_grade(row.iloc[4]), "수학": safe_grade(row.iloc[8]), 
                        "영어": safe_grade(row.iloc[10]), "한국사": safe_grade(row.iloc[12]), 
                        "탐구1": safe_grade(row.iloc[13]) or safe_grade(row.iloc[16]), "탐구2": safe_grade(row.iloc[14]) or safe_grade(row.iloc[21])
                    })
            except: continue
        m_df = pd.DataFrame(m_res).sort_values("key").drop(columns="key") if m_res else pd.DataFrame()
    return i_df, m_df

def extract_section(text, start_keyword, end_keyword=None):
    if end_keyword: pattern = rf"\[{start_keyword}\].*?(?=\[{end_keyword}\]|$)"
    else: pattern = rf"\[{start_keyword}\].*"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    return re.sub(rf"^.*?\[{start_keyword}\].*?(?=\n|$)", "", match.group(0).strip(), flags=re.IGNORECASE).strip() if match else ""

# ==========================================
# 4. 메인 UI
# ==========================================
st.set_page_config(page_title="고3 대입 전문 컨설팅 시스템", layout="wide")
st.title("🎓 고3 대입 전문 컨설팅 시스템")

with st.sidebar:
    st.header("📋 학생 데이터 입력")
    target_major = st.text_input("희망 학과", placeholder="예: 신소재공학과")
    excel_file = st.file_uploader("1. 성적 엑셀", type=["xlsx"])
    pdf_file = st.file_uploader("2. 생기부 PDF", type="pdf")
    is_rural = st.checkbox("🌾 농어촌 전형 대상자 여부", value=False)
    st.divider()
    if st.button("💾 데이터 저장"):
        st.success("데이터가 반영되었습니다.")

# ==========================================
# 5. 분석 로직 (태그 생성 강화)
# ==========================================
if excel_file and pdf_file and target_major:
    if not st.session_state.analysis_result:
        with st.spinner('🚀 입시 전문가 AI가 분석 중입니다...'):
            i_df, m_df = process_performance_data(excel_file.getvalue())
            with pdfplumber.open(pdf_file) as p: pdf_text = "".join([pg.extract_text() for pg in p.pages])
            k_base = sync_knowledge()
            
            prompt = f"""
            입시 컨설턴트로서 {target_major} 지망 학생 분석. 농어촌: {is_rural}
            데이터: 내신({i_df.to_string()}), 모의고사({m_df.to_string()}), 생기부({pdf_text[:10000]}), 지식({k_base[:5000]})
            
            [규칙] 모든 내용은 개괄식 음슴체 사용. 생기부 역량 점수(@RADAR)가 낮으면 교과전형 위주 추천.
            답변 마지막에 반드시 아래 형식의 태그를 포함할 것.
            @PIE [교과: 70, 종합: 20, 정시: 10] @
            @RADAR [전공적합성: 80, 학업역량: 75, 진로탐색: 85, 리더십/인성: 70, 발전가능성: 80] @

            [분석 항목]
            [PART 1] 종합 진단: 등급 분석 및 세특 부실 지적.
            [PART 2] 대입 전략: 전형별 전략, 농어촌 전략(로또성 경고), 생기부 보완책, 추천 도서.
            [PART 3] 심화 탐구 및 세특 예시: 주제/근거(학년-학기-활동명 명시)/방법 3개 및 NEIS용 세특 문구 3개.
            [PART 4] 면접 예상 질문: 질문/답안/준비 3개.
            """
            response = model.generate_content(prompt)
            st.session_state.analysis_result = response.text
            st.session_state.i_df, st.session_state.m_df = i_df, m_df

    res = st.session_state.analysis_result
    clean_res = re.sub(r'@.*?@', '', res, flags=re.DOTALL).strip()
    
    p1 = extract_section(clean_res, "PART 1", "PART 2")
    p2 = extract_section(clean_res, "PART 2", "PART 3")
    p3 = extract_section(clean_res, "PART 3", "PART 4")
    p4 = extract_section(clean_res, "PART 4")

    # 가독성 변환
    p3 = re.sub(r'(?i)주제\s*:', '#### 📍 주제:', p3)
    p3 = re.sub(r'(?i)종적/횡적\s*근거\s*:', '🔍 **종적/횡적 근거:**', p3)
    p3 = re.sub(r'(?i)탐구\s*방법\s*:', '🛠️ **탐구 방법:**', p3)
    p4 = re.sub(r'(?i)질문\s*:', '#### ❓ 질문:', p4)
    p4 = re.sub(r'(?i)모범\s*답안\s*:', '✅ **모범 답안:**', p4)

    def render_all_charts(suffix):
        c1, c2 = st.columns(2); c3, c4 = st.columns(2)
        if not st.session_state.i_df.empty:
            c1.plotly_chart(px.line(st.session_state.i_df, x="학기", y="등급", markers=True, range_y=[9, 1], title="내신 등급 추이"), use_container_width=True, key=f"i_{suffix}")
        if not st.session_state.m_df.empty:
            fig_m = px.line(st.session_state.m_df, x="시험", y=["국어", "수학", "영어", "한국사", "탐구1", "탐구2"], markers=True, range_y=[9, 1], title="모의고사 등급 추이", labels={"value":"등급","variable":"과목"})
            fig_m.update_traces(connectgaps=True); c2.plotly_chart(fig_m, use_container_width=True, key=f"m_{suffix}")
        
        p_m = re.search(r'@PIE\s*\[(.*?)\]\s*@', res, re.IGNORECASE)
        if p_m:
            items = [it.split(':') for it in p_m.group(1).split(',')]
            p_df = pd.DataFrame([{"전형": k.strip(), "비중": int(re.sub(r'[^0-9]', '', v))} for k, v in items])
            c3.plotly_chart(px.pie(p_df, values="비중", names="전형", hole=0.4, title="추천 전형"), use_container_width=True, key=f"p_{suffix}")
        
        r_m = re.search(r'@RADAR\s*\[(.*?)\]\s*@', res, re.IGNORECASE)
        if r_m:
            items = [it.split(':') for it in r_m.group(1).split(',')]
            lbls = [k.strip() for k, v in items]; vls = [int(re.sub(r'[^0-9]', '', v)) for k, v in items]
            fig_r = go.Figure(data=go.Scatterpolar(r=vls + [vls[0]], theta=lbls + [lbls[0]], fill='toself'))
            fig_r.update_layout(polar=dict(radialaxis=dict(visible=True, range=[0, 100])), title="생기부 종합 역량")
            c4.plotly_chart(fig_r, use_container_width=True, key=f"r_{suffix}")

    tab1, tab2, tab3, tab4 = st.tabs(["📊 진단/전략", "💡 가이드", "💬 상담", "🖨️ 인쇄"])

    with tab1:
        render_all_charts("tab1")
        st.markdown(f"### 📝 종합 진단\n{p1}\n### 🎯 대입 전략\n{p2}")
    with tab2:
        st.markdown(f"### 🚀 탐구 및 세특 문구\n{p3}\n### 🎤 면접 질문\n{p4}")
    with tab3:
        for msg in st.session_state.chat_history:
            with st.chat_message(msg["role"]): st.markdown(msg["content"])
        if p_chat := st.chat_input("질문하세요..."):
            st.session_state.chat_history.append({"role": "user", "content": p_chat})
            with st.chat_message("assistant"):
                ans = model.generate_content(f"배경: {res}\n질문: {p_chat}"); st.markdown(ans.text)
                st.session_state.chat_history.append({"role": "assistant", "content": ans.text})
    with tab4:
        st.info("💡 Ctrl + P 를 눌러 인쇄하세요. (배경 그래픽 체크 필수)")
        st.markdown(f"## 🎓 대입 컨설팅 종합 리포트 ({target_major})")
        render_all_charts("print")
        st.divider()
        st.markdown(f"### 📝 종합 진단\n{p1}\n### 🎯 대입 전략\n{p2}\n### 🚀 심화 탐구\n{p3}\n### 🎤 면접 질문\n{p4}")
