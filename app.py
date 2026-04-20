import streamlit as st
import pandas as pd
import plotly.express as px
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
    # 선생님 환경에서 성공한 최신 모델명으로 고정 (NotFound 에러 해결)
    model = genai.GenerativeModel("gemini-2.5-flash") 
except Exception as e:
    st.error(f"⚠️ 시스템 설정 오류: {e}")

if "analysis_result" not in st.session_state: st.session_state.analysis_result = ""
if "chat_history" not in st.session_state: st.session_state.chat_history = []

# ==========================================
# 2. 화면 스타일 설정
# ==========================================
st.markdown("""
    <style>
    .stApp { background-color: #f8f9fa; }
    .main .block-container { padding-top: 2rem; }
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
                    eng = str(row.iloc[10]); eng_v = re.search(r'[1-9]', eng); eng_s = 100 - (int(eng_v.group()) - 1) * 10 if eng_v else 0
                    m_res.append({"key": int(f"{d_m.group(1)}{d_m.group(2)}"), "시험": f"{g_m.group(1)}학년 {d_m.group(2)}월", "국어": float(row.iloc[4]), "수학": float(row.iloc[8]), "영어": eng_s, "탐구": float(row.iloc[13])})
            except: continue
        m_df = pd.DataFrame(m_res).sort_values("key").drop(columns="key") if m_res else pd.DataFrame()
    return i_df, m_df

def extract_section(text, start_keyword, end_keyword=None):
    if end_keyword: pattern = rf"\[{start_keyword}\].*?(?=\[{end_keyword}\]|$)"
    else: pattern = rf"\[{start_keyword}\].*"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    return match.group(0).strip() if match else ""

# ==========================================
# 4. 메인 UI 구성
# ==========================================
st.set_page_config(page_title="고3 대입 전문 컨설팅", layout="wide")
st.title("🎓 고3 대입 전문 컨설팅 시스템")

with st.sidebar:
    st.header("📋 데이터 업로드")
    target_major = st.text_input("🎯 희망 학과", placeholder="예: 신소재공학과")
    excel_file = st.file_uploader("1️⃣ 성적 엑셀", type=["xlsx"])
    pdf_file = st.file_uploader("2️⃣ 생기부 PDF", type="pdf")
    # 농어촌 전형 체크박스 유지
    is_rural = st.checkbox("🌾 농어촌 전형 대상자", value=False)
    
    st.divider()
    if st.button("💾 학교 데이터 저장"):
        st.info("기능이 활성화되었습니다.")

# ==========================================
# 5. 분석 실행
# ==========================================
if excel_file and pdf_file and target_major:
    if not st.session_state.analysis_result:
        with st.spinner('🚀 최신 AI 엔진으로 분석 중...'):
            i_df, m_df = process_performance_data(excel_file.getvalue())
            with pdfplumber.open(pdf_file) as p: pdf_text = "".join([pg.extract_text() for pg in p.pages])
            k_base = sync_knowledge()
            
            rural_note = "이 학생은 [농어촌 전형] 대상자임. 유리한 전략 제시할 것." if is_rural else ""
            
            prompt = f"""
            입시 컨설턴트로서 {target_major} 지망 학생 분석. {rural_note}
            데이터: 내신({i_df.to_string()}), 모의고사({m_df.to_string()}), 생기부({pdf_text[:12000]}), 지식({k_base[:5000]})
            음슴체 사용. [PART 1] 진단, [PART 2] 전략, [PART 3] 탐구주제, [PART 4] 면접질문.
            마지막에 @PIE [교과: 60, 정시: 10, 종합: 30] @ 포함.
            """
            
            try:
                response = model.generate_content(prompt)
                st.session_state.analysis_result = response.text
                st.session_state.i_df, st.session_state.m_df = i_df, m_df
            except Exception as e:
                st.error(f"❌ 분석 실패: {e}")

    # 결과 대시보드 표시
    if st.session_state.analysis_result:
        res = st.session_state.analysis_result
        clean_res = re.sub(r'@.*?@', '', res, flags=re.DOTALL).strip()
        tab1, tab2, tab3 = st.tabs(["📊 진단 및 전략", "💡 탐구/면접 가이드", "💬 추가 상담"])

        with tab1:
            c1, c2 = st.columns(2)
            if not st.session_state.i_df.empty: c1.plotly_chart(px.line(st.session_state.i_df, x="학기", y="등급", markers=True, range_y=[9, 1], title="내신"))
            if not st.session_state.m_df.empty: c2.plotly_chart(px.line(st.session_state.m_df, x="시험", y=["국어", "수학", "영어", "탐구"], markers=True, title="모의고사"))
            st.markdown(extract_section(clean_res, "PART 1", "PART 2"))
            st.markdown(extract_section(clean_res, "PART 2", "PART 3"))

        with tab2:
            st.markdown(extract_section(clean_res, "PART 3", "PART 4"))
            st.divider()
            st.markdown(extract_section(clean_res, "PART 4"))

        with tab3:
            for msg in st.session_state.chat_history:
                with st.chat_message(msg["role"]): st.markdown(msg["content"])
            if p_chat := st.chat_input("AI에게 더 물어보세요..."):
                st.session_state.chat_history.append({"role": "user", "content": p_chat})
                with st.chat_message("user"): st.markdown(p_chat)
                with st.chat_message("assistant"):
                    ans = model.generate_content(f"상황: {res}\n질문: {p_chat}")
                    st.markdown(ans.text); st.session_state.chat_history.append({"role": "assistant", "content": ans.text})
else:
    st.info("👈 왼쪽에서 파일과 정보를 입력하면 분석이 시작됩니다.")
