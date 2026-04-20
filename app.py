import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import pdfplumber
import requests
import re
import io
import urllib.parse
import google.generativeai as genai

# ==========================================
# 1. 보안 및 API 설정
# ==========================================
try:
    API_KEY = st.secrets["GEMINI_API_KEY"]
    GSHEET_SCRIPT_URL = st.secrets["GSHEET_SCRIPT_URL"]
    
    genai.configure(api_key=API_KEY)
    # 선생님 환경에서 검증된 최신 모델 사용
    model = genai.GenerativeModel("gemini-2.5-flash") 
except Exception as e:
    st.error(f"⚠️ 시스템 설정 오류: {e}")

if "analysis_result" not in st.session_state: st.session_state.analysis_result = ""
if "chat_history" not in st.session_state: st.session_state.chat_history = []

# ==========================================
# 2. 화면 및 인쇄 스타일 (그래프 인쇄 최적화)
# ==========================================
st.markdown("""
    <style>
    .stApp { background-color: #ffffff; }
    
    @media print {
        [data-testid="stSidebar"], header, footer, .stChatInput, .no-print {
            display: none !important;
        }
        .stTabs [role="tablist"] {
            display: none !important;
        }
        .main .block-container {
            max-width: 100% !important;
            padding: 0 !important;
        }
        h2 { border-bottom: 2px solid black; padding-bottom: 5px; }
        p, li { font-size: 11pt !important; color: #111 !important; line-height: 1.6; }
        @page { margin: 1.5cm; }
    }
    </style>
    """, unsafe_allow_html=True)

# ==========================================
# 3. 데이터 가공 함수들 (전 과목 등급 추출 강화)
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

    def get_val(row, idx):
        return safe_grade(row.iloc[idx]) if idx < len(row) else None

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
                txt = str(row.iloc[0])
                g_m = re.search(r'(\d)학년', txt)
                d_m = re.search(r'\((\d{2})-(\d{2})\)', txt)
                if g_m and d_m:
                    m_res.append({
                        "key": int(f"{d_m.group(1)}{d_m.group(2)}"), 
                        "시험": f"{g_m.group(1)}학년 {d_m.group(2)}월", 
                        "국어": get_val(row, 4), "수학": get_val(row, 8), 
                        "영어": get_val(row, 10), "한국사": get_val(row, 12), 
                        "탐구1": get_val(row, 13) or get_val(row, 16), 
                        "탐구2": get_val(row, 14) or get_val(row, 21)
                    })
            except: continue
        m_df = pd.DataFrame(m_res).sort_values("key").drop(columns="key") if m_res else pd.DataFrame()
    return i_df, m_df

def extract_section(text, start_keyword, end_keyword=None):
    if end_keyword: pattern = rf"\[{start_keyword}\].*?(?=\[{end_keyword}\]|$)"
    else: pattern = rf"\[{start_keyword}\].*"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    if not match: return ""
    content = match.group(0).strip()
    content = re.sub(rf"^.*?\[{start_keyword}\].*?(?=\n|$)", "", content, flags=re.IGNORECASE).strip()
    return content

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
    st.header("📚 지식 데이터베이스")
    ref_file = st.file_uploader("자료 업로드", type=["pdf", "xlsx"])
    if st.button("💾 영구 저장"):
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
# 5. 분석 및 출력 로직
# ==========================================
if excel_file and pdf_file and target_major:
    if not st.session_state.analysis_result:
        with st.spinner('AI 엔진이 데이터를 정밀 분석 중입니다...'):
            i_df, m_df = process_performance_data(excel_file.getvalue())
            with pdfplumber.open(pdf_file) as p: pdf_text = "".join([pg.extract_text() for pg in p.pages])
            k_base = sync_knowledge()
            
            rural_inst = "이 학생은 [농어촌 전형] 대상자임." if is_rural else ""
            
            prompt = f"""
            전문 입시 컨설턴트로서 {target_major} 지망 학생을 분석하세요. {rural_inst}
            데이터: 내신({i_df.to_string()}), 모의고사({m_df.to_string()}), 생기부({pdf_text[:12000]}), 지식({k_base[:5000]})
            
            [수치 해석 필수 규칙]
            제공된 표의 내신/모의고사 수치는 '등급'임. 숫자가 작을수록 우수함.
            절대 "점수"라고 표현하지 말고 "등급" 단위로만 분석할 것.

            [절대 준수 규칙]
            1. 인사말 금지. [PART 1]부터 개괄식 음슴체로 즉시 출력.
            2. [PART 1] 전공 관련 세특 누락 지적 필수.
            3. 마지막에 @PIE [...] @ 및 @RADAR [...] @ 태그 포함.
            """
            response = model.generate_content(prompt)
            st.session_state.analysis_result = response.text
            st.session_state.i_df, st.session_state.m_df = i_df, m_df

    # --- 데이터 후처리 ---
    res = st.session_state.analysis_result
    clean_res = re.sub(r'@.*?@', '', res, flags=re.DOTALL).strip()

    p1 = extract_section(clean_res, "PART 1", "PART 2")
    p2 = extract_section(clean_res, "PART 2", "PART 3")
    p3 = extract_section(clean_res, "PART 3", "PART 4")
    p4 = extract_section(clean_res, "PART 4")

    p3 = re.sub(r'(?i)주제\s*:', '#### 📍 주제:', p3)
    p3 = re.sub(r'(?i)종적/횡적\s*근거\s*:', '🔍 **종적/횡적 근거:**', p3)
    p3 = re.sub(r'(?i)탐구\s*방법\s*:', '🛠️ **탐구 방법:**', p3)
    p4 = re.sub(r'(?i)질문\s*:', '#### ❓ 질문:', p4)
    p4 = re.sub(r'(?i)모범\s*답안\s*:', '✅ **모범 답안:**', p4)
    p4 = re.sub(r'(?i)준비\s*방법\s*:', '🛠️ **준비 방법:**', p4)

    # 그래프 객체 생성
    fig_i = px.line(st.session_state.i_df, x="학기", y="등급", markers=True, range_y=[9, 1], title="내신 등급 추이")
    fig_m = px.line(st.session_state.m_df, x="시험", y=["국어", "수학", "영어", "한국사", "탐구1", "탐구2"], markers=True, range_y=[9, 1], title="모의고사 등급 추이", labels={"value":"등급", "variable":"과목"})
    fig_m.update_traces(connectgaps=True)

    # 전형 비중 도넛 차트
    pie_raw = re.search(r'@PIE\s*\[(.*?)\]\s*@', res)
    try:
        p_items = [it.split(':') for it in pie_raw.group(1).split(',')]
        p_df = pd.DataFrame([{"전형": k.strip(), "비중": int(re.sub(r'[^0-9]', '', v))} for k, v in p_items])
        fig_p = px.pie(p_df, values="비중", names="전형", hole=0.4, title="추천 전형 비중")
    except: fig_p = None

    # 생기부 역량 레이더 차트
    radar_raw = re.search(r'@RADAR\s*\[(.*?)\]\s*@', res)
    try:
        r_items = [it.split(':') for it in radar_raw.group(1).split(',')]
        r_labels = [k.strip() for k, v in r_items]; r_values = [int(re.sub(r'[^0-9]', '', v)) for k, v in r_items]
        fig_r = go.Figure(data=go.Scatterpolar(r=r_values + [r_values[0]], theta=r_labels + [r_labels[0]], fill='toself'))
        fig_r.update_layout(polar=dict(radialaxis=dict(visible=True, range=[0, 100])), title="생기부 종합 역량")
    except: fig_r = None

    # --- 화면 출력부 ---
    tab1, tab2, tab3, tab4 = st.tabs(["📝 진단 및 전략", "🚀 심화 탐구 가이드", "💬 실시간 상담", "🖨️ 리포트 인쇄"])

    with tab1:
        st.subheader("📊 데이터 분석 대시보드")
        c1, c2 = st.columns(2); c3, c4 = st.columns(2)
        c1.plotly_chart(fig_i, use_container_width=True, key="i1")
        c2.plotly_chart(fig_m, use_container_width=True, key="m1")
        if fig_p: c3.plotly_chart(fig_p, use_container_width=True, key="p1")
        if fig_r: c4.plotly_chart(fig_r, use_container_width=True, key="r1")
        st.divider()
        st.markdown(f"### 📝 [PART 1] 종합 진단\n\n{p1}")
        st.markdown(f"### 🎯 [PART 2] 대입 전략 및 추천 도서\n\n{p2}")

    with tab2:
        st.markdown(f"### 🚀 [PART 3] 심화 탐구 가이드\n\n{p3}")
        st.divider()
        st.markdown(f"### 🎤 [PART 4] 면접 예상 질문\n\n{p4}")

    with tab3:
        for msg in st.session_state.chat_history:
            with st.chat_message(msg["role"]): st.markdown(msg["content"])
        if p_chat := st.chat_input("질문 입력..."):
            st.session_state.chat_history.append({"role": "user", "content": p_chat})
            with st.chat_message("user"): st.markdown(p_chat)
            with st.chat_message("assistant"):
                ans = model.generate_content(f"배경: {res}\n질문: {p_chat}")
                st.markdown(ans.text); st.session_state.chat_history.append({"role": "assistant", "content": ans.text})

    with tab4:
        st.markdown("""
        <div class="no-print" style="margin-bottom: 20px;">
            <a href="javascript:window.print()" style="display: inline-block; padding: 12px 24px; background-color: #2e6bc6; color: white; text-decoration: none; border-radius: 8px; font-weight: bold; font-size: 16px;">🖨️ 리포트 인쇄하기</a>
            <p style="margin-top: 10px; color: #555; font-size: 14px;">※ 설정에서 <b>'배경 그래픽'</b>을 체크해 주세요.</p>
        </div>
        """, unsafe_allow_html=True)
        st.markdown(f"## 🎓 대입 컨설팅 리포트 ({target_major})")
        pc1, pc2 = st.columns(2); pc3, pc4 = st.columns(2)
        pc1.plotly_chart(fig_i, use_container_width=True, key="i2")
        pc2.plotly_chart(fig_m, use_container_width=True, key="m2")
        if fig_p: pc3.plotly_chart(fig_p, use_container_width=True, key="p2")
        if fig_r: pc4.plotly_chart(fig_r, use_container_width=True, key="r2")
        st.divider()
        st.markdown(f"### 📝 [PART 1] 종합 진단\n\n{p1}\n\n### 🎯 [PART 2] 대입 전략\n\n{p2}\n\n### 🚀 [PART 3] 심화 탐구\n\n{p3}\n\n### 🎤 [PART 4] 면접 질문\n\n{p4}")
else:
    st.info("👈 왼쪽에서 정보를 입력하고 파일을 업로드해 주세요.")
