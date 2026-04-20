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
    # 스트림릿 Secrets에서 정보 가져오기
    API_KEY = st.secrets["GEMINI_API_KEY"]
    GSHEET_SCRIPT_URL = st.secrets["GSHEET_SCRIPT_URL"]
    
    # Gemini 설정
    genai.configure(api_key=API_KEY)
    # 45만원 크레딧 소진을 위해 정식 모델명 사용
    model = genai.GenerativeModel("gemini-1.5-flash") 
except Exception as e:
    st.error(f"⚠️ 설정 오류: {e}\n스트림릿 Secrets를 확인해 주세요.")

if "analysis_result" not in st.session_state: st.session_state.analysis_result = ""
if "chat_history" not in st.session_state: st.session_state.chat_history = []

# ==========================================
# 2. 화면 스타일 설정 (CSS)
# ==========================================
st.markdown("""
    <style>
    /* 기본 배경 및 폰트 설정 */
    .stApp { background-color: #f8f9fa; }
    .stMarkdown p { font-size: 1.1rem; line-height: 1.7; }
    
    /* 인쇄 시 불필요한 요소 제거 */
    @media print {
        [data-testid="stSidebar"], header, footer, .stTabs, button, .stChatInput { display: none !important; }
        .print-only { display: block !important; visibility: visible !important; }
    }
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
                txt = str(row.iloc[0])
                g_m, d_m = re.search(r'(\d)학년', txt), re.search(r'\((\d{2})-(\d{2})\)', txt)
                if g_m and d_m:
                    eng = str(row.iloc[10]); eng_v = re.search(r'[1-9]', eng)
                    eng_s = 100 - (int(eng_v.group()) - 1) * 10 if eng_v else 0
                    m_res.append({
                        "key": int(f"{d_m.group(1)}{d_m.group(2)}"), 
                        "시험": f"{g_m.group(1)}학년 {d_m.group(2)}월", 
                        "국어": float(row.iloc[4]), "수학": float(row.iloc[8]), 
                        "영어": eng_s, "탐구": float(row.iloc[13])
                    })
            except: continue
        m_df = pd.DataFrame(m_res).sort_values("key").drop(columns="key") if m_res else pd.DataFrame()
    return i_df, m_df

def extract_section(text, start_keyword, end_keyword=None):
    if end_keyword: pattern = rf"\[{start_keyword}\].*?(?=\[{end_keyword}\]|$)"
    else: pattern = rf"\[{start_keyword}\].*"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    return match.group(0).strip() if match else ""

# ==========================================
# 4. 메인 UI 구성 (입력 도구)
# ==========================================
st.set_page_config(page_title="고3 대입 전문 컨설팅", layout="wide")
st.title("🎓 고3 대입 전문 컨설팅 시스템")

with st.sidebar:
    st.header("📋 학생 데이터 업로드")
    target_major = st.text_input("🎯 희망 학과", placeholder="예: 신소재공학과")
    excel_file = st.file_uploader("1️⃣ 성적 엑셀 파일 (.xlsx)", type=["xlsx"])
    pdf_file = st.file_uploader("2️⃣ 생활기록부 PDF", type="pdf")
    
    st.divider()
    st.header("📚 학교 특화 지식고")
    ref_file = st.file_uploader("참고 자료 업로드", type=["pdf", "xlsx"])
    if st.button("💾 데이터베이스 저장"):
        if ref_file:
            with st.spinner("지식 동기화 중..."):
                txt = f"\n[자료: {ref_file.name}]\n"
                if ref_file.name.endswith(".pdf"):
                    with pdfplumber.open(ref_file) as p: txt += "".join([pg.extract_text() for pg in p.pages])
                else:
                    xls_ref = pd.ExcelFile(ref_file)
                    for s in xls_ref.sheet_names: txt += f"\n- {s} -\n{pd.read_excel(xls_ref, s).to_string()}\n"
                sync_knowledge(txt); st.success("성공적으로 저장되었습니다!")

# ==========================================
# 5. 분석 엔진 (AI 호출 및 결과 처리)
# ==========================================
if excel_file and pdf_file and target_major:
    if not st.session_state.analysis_result:
        with st.spinner('🚀 AI가 학생 데이터를 정밀 분석 중입니다...'):
            # 데이터 추출
            i_df, m_df = process_performance_data(excel_file.getvalue())
            with pdfplumber.open(pdf_file) as p: pdf_text = "".join([pg.extract_text() for pg in p.pages])
            k_base = sync_knowledge()
            
            # AI 프롬프트 구성
            prompt = f"""
            입시 컨설턴트로서 {target_major} 지망 학생을 분석하라.
            데이터: 내신({i_df.to_string()}), 모의고사({m_df.to_string()}), 생기부({pdf_text[:12000]}), 지식({k_base[:5000]})
            [출력 지침] 
            1. 문장은 음슴체(~함)로 작성. 
            2. [PART 1] 성적 및 전형 진단, [PART 2] 입시 전략, [PART 3] 심화 탐구 주제, [PART 4] 면접 예상 질문 순서로 작성.
            3. 마지막에 반드시 @PIE [교과: 60, 정시: 10, 종합: 30] @ 형식으로 비중 포함.
            """
            
            # AI 실행
            response = model.generate_content(prompt)
            st.session_state.analysis_result = response.text
            st.session_state.i_df, st.session_state.m_df = i_df, m_df

    # --- 대시보드 출력 ---
    res = st.session_state.analysis_result
    clean_res = re.sub(r'@.*?@', '', res, flags=re.DOTALL).strip()
    
    tab1, tab2, tab3 = st.tabs(["📊 진단 및 전략", "💡 탐구/면접 가이드", "💬 추가 상담"])

    with tab1:
        st.subheader("📈 성적 추이 분석")
        c1, c2 = st.columns(2)
        if not st.session_state.i_df.empty: 
            c1.plotly_chart(px.line(st.session_state.i_df, x="학기", y="등급", markers=True, range_y=[9, 1], title="교과 내신 추이"), use_container_width=True)
        if not st.session_state.m_df.empty:
            c2.plotly_chart(px.line(st.session_state.m_df, x="시험", y=["국어", "수학", "영어", "탐구"], markers=True, title="모의고사 성적 추이"), use_container_width=True)
        
        st.divider()
        st.markdown(extract_section(clean_res, "PART 1", "PART 2"))
        st.markdown(extract_section(clean_res, "PART 2", "PART 3"))

    with tab2:
        st.markdown(extract_section(clean_res, "PART 3", "PART 4"))
        st.divider()
        st.markdown(extract_section(clean_res, "PART 4"))

    with tab3:
        st.info("AI와 실시간으로 대화하며 궁금한 점을 해결하세요.")
        for msg in st.session_state.chat_history:
            with st.chat_message(msg["role"]): st.markdown(msg["content"])
        if p_chat := st.chat_input("질문을 입력하세요..."):
            st.session_state.chat_history.append({"role": "user", "content": p_chat})
            with st.chat_message("user"): st.markdown(p_chat)
            with st.chat_message("assistant"):
                ans = model.generate_content(f"상황: {res}\n질문: {p_chat}")
                st.markdown(ans.text)
                st.session_state.chat_history.append({"role": "assistant", "content": ans.text})
else:
    st.warning("👈 왼쪽 사이드바에서 학과를 입력하고 파일을 모두 업로드해 주세요.")
