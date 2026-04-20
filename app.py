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
# 2. 인쇄 및 화면 스타일 (그래프 보호 및 다중 페이지 강제 해제)
# ==========================================
st.markdown("""
    <style>
    .stApp { background-color: #ffffff; }
    
    @media print {
        /* 1. 불필요한 UI 숨기기 */
        [data-testid="stSidebar"], header, footer, .stChatInput, .no-print, [data-baseweb="tab-list"], .stActionButton {
            display: none !important;
        }

        /* 2. [핵심] 1페이지 잘림 방지: 스크롤 컨테이너 강제 해제 */
        html, body, .stApp, 
        [data-testid="stAppViewContainer"], 
        [data-testid="stMainBlockContainer"],
        [data-testid="stVerticalBlock"],
        .main, .block-container {
            display: block !important;
            height: auto !important;
            min-height: auto !important;
            overflow: visible !important;
            position: static !important;
        }

        /* 3. [중요] 그래프 내부 overflow는 건드리지 않음 (원형/방사형 증발 방지) */
        .js-plotly-plot, .plotly, .main-svg {
            overflow: hidden !important; 
        }

        /* 4. 레이아웃 최적화 */
        .block-container { max-width: 100% !important; padding: 0 !important; margin: 0 !important; }
        
        /* 5. 폰트 및 가독성 */
        h2 { text-align: center; border-bottom: 2px solid black; padding-bottom: 10px; }
        h3 { border-left: 10px solid #1a73e8; padding-left: 15px; margin-top: 30px; page-break-after: avoid; }
        p, li { font-size: 11.5pt !important; line-height: 1.7; color: #000; }
        
        @page { margin: 1.5cm; }
    }
    li { margin-bottom: 8px; }
    </style>
    """, unsafe_allow_html=True)

# ==========================================
# 3. 데이터 가공 함수들
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
    if not match: return ""
    return re.sub(rf"^.*?\[{start_keyword}\].*?(?=\n|$)", "", match.group(0).strip(), flags=re.IGNORECASE).strip()

# ==========================================
# 4. 메인 UI 구성
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
    ref_file = st.file_uploader("지식 데이터베이스 업로드", type=["pdf", "xlsx"])
    if st.button("💾 데이터 저장"):
        if ref_file:
            with st.spinner("저장 중..."):
                txt = f"\n[자료: {ref_file.name}]\n"
                if ref_file.name.endswith(".pdf"):
                    with pdfplumber.open(ref_file) as p: txt += "".join([pg.extract_text() for pg in p.pages])
                else:
                    xls_ref = pd.ExcelFile(ref_file)
                    for s in xls_ref.sheet_names: txt += f"\n- {s} -\n{pd.read_excel(xls_ref, s).to_string()}\n"
                sync_knowledge(txt); st.success("동기화 완료!")

# ==========================================
# 5. 분석 엔진 (심층 분석 품질 복구)
# ==========================================
if excel_file and pdf_file and target_major:
    if not st.session_state.analysis_result:
        with st.spinner('🚀 입시 전문가 AI가 정밀 분석 중입니다...'):
            i_df, m_df = process_performance_data(excel_file.getvalue())
            with pdfplumber.open(pdf_file) as p: pdf_text = "".join([pg.extract_text() for pg in p.pages])
            k_base = sync_knowledge()
            
            prompt = f"""
            전문 입시 컨설턴트로서 {target_major} 지망 학생을 분석하세요. 농어촌: {is_rural}
            데이터: 내신({i_df.to_string()}), 모의고사({m_df.to_string()}), 생기부({pdf_text[:12000]}), 지식({k_base[:5000]})
            
            [분석 및 출력 원칙]
            1. 모든 내용은 개괄식 음슴체 사용. 줄글 작성 절대 금지.
            2. 내신/모의고사 수치는 '등급'임. 하락 시 '등급' 단위로 분석할 것.
            3. 생기부 역량(@RADAR)이 낮으면 교과전형 위주 추천, 높으면 종합전형 위주 추천.
            4. 답변 마지막에 @PIE [...] @ 및 @RADAR [...] @ 태그 필수 생성.

            [작성 항목]
            [PART 1] 종합 진단: 전 과목 등급 추이 심층 분석(10줄 이상). 희망 학과 관련 핵심 과목 세특의 누락/부실 여부를 날카롭게 지적할 것.
            [PART 2] 대입 전략: 전형별 액션 플랜, 농어촌 전략(로또성 경고 및 일반전형과 비교), 생기부 보완 전략(남은 기간 구체적 활동 제언), 추천 도서(간결하게).
            [PART 3] 심화 탐구 및 세특 예시: 주제/근거/방법 3개. '종적/횡적 근거'는 반드시 생기부에서 'X학년 X학기 OO활동' 등 구체적 출처 인용. NEIS 기재용 세특 문구 3개(전문가 수준 문장).
            [PART 4] 면접 예상 질문: 질문/답안/준비 가이드 3개.
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

    # 가시성 강화
    p2 = re.sub(r'(?i)농어촌\s*전형\s*전략', '⚖️ **농어촌 전형 전략**', p2)
    p2 = re.sub(r'(?i)생기부\s*보완\s*전략', '🛠️ **생기부 보완 전략**', p2)
    p3 = re.sub(r'(?i)주제\s*:', '#### 📍 주제:', p3)
    p3 = re.sub(r'(?i)종적/횡적\s*근거\s*:', '🔍 **종적/횡적 근거:**', p3)
    p3 = re.sub(r'(?i)탐구\s*방법\s*:', '🛠️ **탐구 방법:**', p3)
    p3 = re.sub(r'(?i)NEIS\s*기재용\s*세특\s*문구\s*예시\s*:', '### ✍️ NEIS 기재용 세특 문구 예시', p3)

    def render_all_charts(suffix):
        c1, c2 = st.columns(2); c3, c4 = st.columns(2)
        if not st.session_state.i_df.empty:
            c1.plotly_chart(px.line(st.session_state.i_df, x="학기", y="등급", markers=True, range_y=[9, 1], title="내신 등급 추이"), use_container_width=True, key=f"i_{suffix}")
        if not st.session_state.m_df.empty:
            fig_m = px.line(st.session_state.m_df, x="시험", y=["국어", "수학", "영어", "한국사", "탐구1", "탐구2"], markers=True, range_y=[9, 1], title="모의고사 등급 추이", labels={"value":"등급","variable":"과목"})
            fig_m.update_traces(connectgaps=True); c2.plotly_chart(fig_m, use_container_width=True, key=f"m_{suffix}")
        
        p_m = re.search(r'@PIE\s*\[(.*?)\]\s*@', res, re.IGNORECASE)
        if p_m:
            try:
                items = [it.split(':') for it in p_m.group(1).split(',')]
                p_df = pd.DataFrame([{"전형": k.strip(), "비중": int(re.sub(r'[^0-9]', '', v))} for k, v in items])
                c3.plotly_chart(px.pie(p_df, values="비중", names="전형", hole=0.4, title="추천 전형"), use_container_width=True, key=f"p_{suffix}")
            except: pass
        
        r_m = re.search(r'@RADAR\s*\[(.*?)\]\s*@', res, re.IGNORECASE)
        if r_m:
            try:
                items = [it.split(':') for it in r_m.group(1).split(',')]
                lbls = [k.strip() for k, v in items]; vls = [int(re.sub(r'[^0-9]', '', v)) for k, v in items]
                fig_r = go.Figure(data=go.Scatterpolar(r=vls + [vls[0]], theta=lbls + [lbls[0]], fill='toself'))
                fig_r.update_layout(polar=dict(radialaxis=dict(visible=True, range=[0, 100])), title="생기부 종합 역량")
                c4.plotly_chart(fig_r, use_container_width=True, key=f"r_{suffix}")
            except: pass

    tab1, tab2, tab3, tab4 = st.tabs(["📊 진단/전략", "💡 가이드", "💬 상담", "🖨️ 인쇄용 리포트"])

    with tab1:
        render_all_charts("tab1")
        st.markdown(f"### 📝 종합 진단\n{p1}\n### 🎯 대입 전략\n{p2}")
    with tab2:
        st.markdown(f"### 🚀 탐구 및 세특 문구\n{p3}\n### 🎤 면접 질문\n{p4}")
    with tab3:
        for msg in st.session_state.chat_history:
            with st.chat_message(msg["role"]): st.markdown(msg["content"])
        if p_chat := st.chat_input("질문을 입력하세요..."):
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
else:
    st.info("👈 왼쪽 사이드바에 정보를 입력하고 파일을 업로드해 주세요.")
