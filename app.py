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
    model = genai.GenerativeModel("gemini-2.5-flash") 
except Exception as e:
    st.error(f"⚠️ 시스템 설정 오류: {e}")

if "analysis_result" not in st.session_state: st.session_state.analysis_result = ""
if "chat_history" not in st.session_state: st.session_state.chat_history = []

# ==========================================
# 2. 화면 및 인쇄 스타일 (다중 페이지 인쇄 완벽 대응)
# ==========================================
st.markdown("""
    <style>
    .stApp { background-color: #ffffff; }
    
    @media print {
        [data-testid="stSidebar"], header, footer, .stChatInput, .no-print, .stTabs [role="tablist"] {
            display: none !important;
        }
        
        html, body, .stApp, .main, .block-container {
            height: auto !important;
            min-height: auto !important;
            overflow: visible !important;
            position: static !important;
        }
        
        .main .block-container { 
            max-width: 100% !important; 
            padding: 0 !important; 
        }
        
        h2, h3, h4 { page-break-after: avoid; }
        p, li { font-size: 11pt !important; line-height: 1.6; color: #111; page-break-inside: avoid; }
        .js-plotly-plot { page-break-inside: avoid; margin-bottom: 20px; }
        
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
                txt = str(row.iloc[0])
                g_m = re.search(r'(\d)학년', txt); d_m = re.search(r'\((\d{2})-(\d{2})\)', txt)
                if g_m and d_m:
                    m_res.append({
                        "key": int(f"{d_m.group(1)}{d_m.group(2)}"), 
                        "시험": f"{g_m.group(1)}학년 {d_m.group(2)}월", 
                        "국어": safe_grade(row.iloc[4]), "수학": safe_grade(row.iloc[8]), 
                        "영어": safe_grade(row.iloc[10]), "한국사": safe_grade(row.iloc[12]), 
                        "탐구1": safe_grade(row.iloc[13]) or safe_grade(row.iloc[16]), 
                        "탐구2": safe_grade(row.iloc[14]) or safe_grade(row.iloc[21])
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
# 5. 분석 엔진 (농어촌 팩트 체크 프롬프트 적용)
# ==========================================
if excel_file and pdf_file and target_major:
    if not st.session_state.analysis_result:
        with st.spinner('🚀 입시 전문가 AI가 학생부를 정밀 분석 중입니다...'):
            i_df, m_df = process_performance_data(excel_file.getvalue())
            with pdfplumber.open(pdf_file) as p: pdf_text = "".join([pg.extract_text() for pg in p.pages])
            k_base = sync_knowledge()
            
            rural_inst = "이 학생은 [농어촌 전형] 대상자임." if is_rural else ""
            
            prompt = f"""
            입시 컨설턴트로서 {target_major} 지망 학생 분석. {rural_inst}
            데이터: 내신({i_df.to_string()}), 모의고사({m_df.to_string()}), 생기부({pdf_text[:12000]}), 지식({k_base[:5000]})
            
            [절대 규칙: 가독성 및 형식]
            1. **줄글 작성 절대 금지.** 모든 내용은 반드시 글머리 기호('-' 또는 '1.', '2.')를 사용한 개괄식으로 작성.
            2. 인사말 금지. [PART 1]부터 즉시 시작. 철저한 음슴체(~함, ~임) 사용.
            3. 마지막 두 줄은 반드시 아래 태그여야 함 (생략 시 오류 발생).
               단, 예시 숫자를 베끼지 말고 **반드시 본인의 분석 결과와 일치하도록 숫자를 계산**하여 넣을 것.
               @PIE [교과: X, 종합: Y, 정시: Z] @ (X, Y, Z에는 실제 비율 숫자 기입, 합계 100)
               @RADAR [전공적합성: A, 학업역량: B, 진로탐색: C, 리더십/인성: D, 발전가능성: E] @ (0~100 숫자)

            [작성 가이드]
            [PART 1] 종합 진단
            - 내신/모의고사 등급 분석 (수치 기반)
            - 전공 관련 세특 누락/부실 지적 필수 (반드시 개괄식)

            [PART 2] 대입 전략, 생기부 보완, 추천 도서
            - 전형별 액션 플랜 (개괄식)
            - **[논리 일치 필수]**: 여기서 가장 유리하다고 추천한 전형이 반드시 마지막 @PIE 태그에서 가장 높은 비율(1위)을 차지해야 함. 텍스트와 그래프 결과가 엇갈리면 안 됨.
            - **[생기부 보완 전략]**: 학생의 현재 생기부에서 누락되거나 빈약한 부분을 정확히 짚고, 어떤 구체적 활동이나 보고서로 채워야 할지 맞춤형 보완책 제시.
            - **[농어촌 전형 전략 (주의!)]**: 농어촌 전형은 매년 입결 컷이 들쭉날쭉하여 표본 변동성이 매우 큰 전형임. 따라서 절대 "무조건 유리하다"고 논리적 비약을 하지 말 것. "안정/적정 카드는 일반 전형으로 잡되, 상향 지원 시 일반 종합 전형보다 합격 가능성을 높이는 카드로 활용하라"는 등 현실적이고 냉정한 전략을 제시할 것.
            - 추천 도서 3권: 도서명과 함께 선정 이유를 '1문장으로 아주 짧고 간결하게' 작성.

            [PART 3] 심화 탐구 및 세특 예시
            - 탐구 가이드(3개): 주제: / 종적/횡적 근거: (생기부 출처 필수) / 탐구 방법:
            - NEIS 기재용 세특 예시(3개): 과목명: / 내용: (각 200자 내외)

            [PART 4] 면접 예상 질문
            - 질문 3개: 질문: / 모범 답안: / 준비 방법:
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

    # 가시성 강화 변환 (아이콘 추가)
    p2 = re.sub(r'(?i)생기부\s*보완\s*전략', '🛠️ **생기부 보완 전략**', p2)
    p2 = re.sub(r'(?i)농어촌\s*전형\s*전략|농어촌\s*전형\s*유불리\s*판단', '⚖️ **농어촌 전형 전략**', p2)

    p3 = re.sub(r'(?i)주제\s*:', '#### 📍 주제:', p3)
    p3 = re.sub(r'(?i)종적/횡적\s*근거\s*:', '🔍 **종적/횡적 근거:**', p3)
    p3 = re.sub(r'(?i)탐구\s*방법\s*:', '🛠️ **탐구 방법:**', p3)
    p3 = re.sub(r'(?i)NEIS\s*기재용\s*세특\s*문구\s*예시\s*:', '### ✍️ NEIS 기재용 세특 문구 예시', p3)
    p3 = re.sub(r'(?i)과목명\s*:', '📘 **과목명:**', p3)
    p3 = re.sub(r'(?i)내용\s*:', '📝 **내용:**', p3)
    
    p4 = re.sub(r'(?i)질문\s*:', '#### ❓ 질문:', p4)
    p4 = re.sub(r'(?i)모범\s*답안\s*:', '✅ **모범 답안:**', p4)
    p4 = re.sub(r'(?i)준비\s*방법\s*:', '🛠️ **준비 방법:**', p4)

    # --- 차트 렌더링 함수 ---
    def render_all_charts(suffix):
        c1, c2 = st.columns(2); c3, c4 = st.columns(2)
        if not st.session_state.i_df.empty:
            c1.plotly_chart(px.line(st.session_state.i_df, x="학기", y="등급", markers=True, range_y=[9, 1], title="내신 등급 추이", labels={"등급":"등급"}), use_container_width=True, key=f"i_{suffix}")
        if not st.session_state.m_df.empty:
            fig_m = px.line(st.session_state.m_df, x="시험", y=["국어", "수학", "영어", "한국사", "탐구1", "탐구2"], markers=True, range_y=[9, 1], title="모의고사 등급 추이", labels={"value":"등급", "variable":"과목"})
            fig_m.update_traces(connectgaps=True)
            c2.plotly_chart(fig_m, use_container_width=True, key=f"m_{suffix}")
        
        p_match = re.search(r'@PIE\s*\[(.*?)\]\s*@', res, re.IGNORECASE)
        if p_match:
            try:
                p_items = [it.split(':') for it in p_match.group(1).split(',')]
                p_df = pd.DataFrame([{"전형": k.strip(), "비중": int(re.sub(r'[^0-9]', '', v))} for k, v in p_items])
                c3.plotly_chart(px.pie(p_df, values="비중", names="전형", hole=0.4, title="추천 전형 비율"), use_container_width=True, key=f"p_{suffix}")
            except: c3.warning("전형 차트 데이터 형식 오류")
        
        r_match = re.search(r'@RADAR\s*\[(.*?)\]\s*@', res, re.IGNORECASE)
        if r_match:
            try:
                r_items = [it.split(':') for it in r_match.group(1).split(',')]
                r_labels = [k.strip() for k, v in r_items]; r_values = [int(re.sub(r'[^0-9]', '', v)) for k, v in r_items]
                fig_r = go.Figure(data=go.Scatterpolar(r=r_values + [r_values[0]], theta=r_labels + [r_labels[0]], fill='toself'))
                fig_r.update_layout(polar=dict(radialaxis=dict(visible=True, range=[0, 100])), title="생기부 종합 역량 진단")
                c4.plotly_chart(fig_r, use_container_width=True, key=f"r_{suffix}")
            except: c4.warning("역량 차트 데이터 형식 오류")

    # --- 탭 구성 ---
    tab1, tab2, tab3, tab4 = st.tabs(["📊 진단 및 전략", "💡 탐구/면접 가이드", "💬 실시간 상담", "🖨️ 리포트 인쇄"])

    with tab1:
        st.subheader("📊 데이터 기반 컨설팅 대시보드")
        render_all_charts("tab1")
        st.divider()
        st.markdown(f"### 📝 [PART 1] 종합 진단\n\n{p1}")
        st.markdown(f"### 🎯 [PART 2] 대입 전략 및 생기부 보완\n\n{p2}")

    with tab2:
        st.markdown(f"### 🚀 [PART 3] 심화 탐구 및 세특 문구\n\n{p3}")
        st.divider()
        st.markdown(f"### 🎤 [PART 4] 면접 예상 질문\n\n{p4}")

    with tab3:
        for msg in st.session_state.chat_history:
            with st.chat_message(msg["role"]): st.markdown(msg["content"])
        if p_chat := st.chat_input("추가 질문을 입력하세요..."):
            st.session_state.chat_history.append({"role": "user", "content": p_chat})
            with st.chat_message("user"): st.markdown(p_chat)
            with st.chat_message("assistant"):
                ans = model.generate_content(f"배경: {res}\n질문: {p_chat}"); st.markdown(ans.text)
                st.session_state.chat_history.append({"role": "assistant", "content": ans.text})

    with tab4:
        st.markdown("""
        <div class="no-print" style="padding: 15px; background-color: #f1f8ff; border-radius: 8px; border-left: 5px solid #1a73e8; margin-bottom: 20px;">
            <h4 style="margin-top: 0; color: #1a73e8;">🖨️ 리포트 다중 페이지 인쇄 방법</h4>
            <p style="margin-bottom: 5px; font-size: 15px; color: #333;"><b>1.</b> 키보드에서 <b>Ctrl + P</b> (Mac은 Cmd + P)를 누르세요.</p>
            <p style="margin-bottom: 0; font-size: 15px; color: #333;"><b>2.</b> 인쇄 설정(더보기)에서 <b>'배경 그래픽(Background graphics)'</b>을 반드시 체크해야 차트가 인쇄됩니다.</p>
        </div>
        """, unsafe_allow_html=True)
        st.markdown(f"## 🎓 대입 컨설팅 종합 리포트 ({target_major})")
        render_all_charts("tab4")
        st.divider()
        st.markdown(f"### 📝 [PART 1] 종합 진단\n\n{p1}\n\n### 🎯 [PART 2] 대입 전략 및 생기부 보완\n\n{p2}\n\n### 🚀 [PART 3] 심화 탐구 및 세특 문구\n\n{p3}\n\n### 🎤 [PART 4] 면접 질문\n\n{p4}")
else:
    st.info("👈 왼쪽 사이드바에 정보를 입력하고 파일을 업로드해 주세요.")
