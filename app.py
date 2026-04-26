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
# 2. 화면 및 인쇄 스타일 (V74 원본 유지)
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
            overflow: visible !important;
        }
        
        .main .block-container { 
            max-width: 100% !important; 
            padding: 0 !important; 
        }
        
        h2, h3, h4 { page-break-after: avoid; }
        p, li { font-size: 11pt !important; line-height: 1.6; color: #111; }
        .js-plotly-plot { margin-bottom: 10px; }
        
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
            v_str = str(val).strip()
            m = re.fullmatch(r'([1-9])(?:\.0)?\s*(?:등급)?', v_str)
            if m: return float(m.group(1))
            nums = re.findall(r'\d+', v_str)
            if len(nums) == 1 and len(nums[0]) == 1 and 1 <= int(nums[0]) <= 9:
                return float(nums[0])
            return None
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
                        u = float(row.iloc[u_c])
                        r_val = safe_grade(row.iloc[r_c])
                        if r_val is not None:
                            u_s += u; w_s += (u * r_val)
                    except: continue
                if u_s > 0: res.append({"학기": f"{int(g)}-{s_idx}", "등급": round(w_s/u_s, 2)})
        i_df = pd.DataFrame(res)
        
    if '수능모의고사' in xls.sheet_names:
        df_m_raw = pd.read_excel(xls, sheet_name='수능모의고사', header=None)
        m_res = []
        grade_cols = []
        for i in range(min(5, len(df_m_raw))):
            for j, val in enumerate(df_m_raw.iloc[i]):
                if str(val).strip() == '등급' and j not in grade_cols:
                    grade_cols.append(j)
        grade_cols.sort()
        
        for _, row in df_m_raw.iterrows():
            try:
                txt = str(row.iloc[0])
                g_m = re.search(r'(\d)학년', txt)
                d_m = re.search(r'\((\d{2})-(\d{2})\)', txt)
                if g_m and d_m:
                    if len(grade_cols) >= 6:
                        국어 = safe_grade(row.iloc[grade_cols[0]])
                        수학 = safe_grade(row.iloc[grade_cols[1]])
                        영어 = safe_grade(row.iloc[grade_cols[2]])
                        한국사 = safe_grade(row.iloc[grade_cols[3]])
                        탐구1 = safe_grade(row.iloc[grade_cols[4]])
                        탐구2 = safe_grade(row.iloc[grade_cols[5]])
                    else:
                        국어 = safe_grade(row.iloc[4] if len(row) > 4 else None)
                        수학 = safe_grade(row.iloc[8] if len(row) > 8 else None)
                        영어 = safe_grade(row.iloc[10] if len(row) > 10 else None)
                        한국사 = safe_grade(row.iloc[12] if len(row) > 12 else None)
                        탐구1 = safe_grade(row.iloc[13] if len(row) > 13 else None) or safe_grade(row.iloc[16] if len(row) > 16 else None)
                        탐구2 = safe_grade(row.iloc[14] if len(row) > 14 else None) or safe_grade(row.iloc[21] if len(row) > 21 else None)
                    if any(x is not None for x in [국어, 수학, 영어, 한국사, 탐구1, 탐구2]):
                        m_res.append({
                            "key": int(f"{d_m.group(1)}{d_m.group(2)}"), 
                            "시험": f"{g_m.group(1)}학년 {d_m.group(2)}월", 
                            "국어": 국어, "수학": 수학, 
                            "영어": 영어, "한국사": 한국사, 
                            "탐구1": 탐구1, "탐구2": 탐구2
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
# ★ HTML 다운로드 생성기
# ==========================================
def create_html_report(target_major, p1, p2, p3, p4, res, i_df, m_df):
    fig_i_html, fig_m_html, fig_p_html, fig_r_html = "", "", "", ""
    if not i_df.empty:
        fig_i = px.line(i_df, x="학기", y="등급", markers=True, range_y=[9, 1], title="내신 등급 추이", template="plotly")
        fig_i_html = fig_i.to_html(full_html=False, include_plotlyjs='cdn')
    if not m_df.empty:
        fig_m = px.line(m_df, x="시험", y=["국어", "수학", "영어", "한국사", "탐구1", "탐구2"], markers=True, range_y=[9, 1], title="모의고사 등급 추이", template="plotly")
        fig_m.update_traces(connectgaps=True)
        fig_m_html = fig_m.to_html(full_html=False, include_plotlyjs=False)
    p_match = re.search(r'@PIE\s*\[(.*?)\]\s*@', res, re.IGNORECASE)
    if p_match:
        try:
            p_items = [it.split(':') for it in p_match.group(1).split(',')]
            p_items = [it for it in p_items if len(it) >= 2]
            p_df = pd.DataFrame([{"전형": it[0].strip(), "비중": int(re.sub(r'[^0-9]', '', it[1]) or 0)} for it in p_items])
            fig_p = px.pie(p_df, values="비중", names="전형", hole=0.4, title="추천 전형 비율", template="plotly")
            fig_p_html = fig_p.to_html(full_html=False, include_plotlyjs=False)
        except: pass
    r_match = re.search(r'@RADAR\s*\[(.*?)\]\s*@', res, re.IGNORECASE)
    if r_match:
        try:
            r_items = [it.split(':') for it in r_match.group(1).split(',')]
            r_items = [it for it in r_items if len(it) >= 2]
            r_labels = [it[0].strip() for it in r_items]
            r_values = [int(re.sub(r'[^0-9]', '', it[1]) or 0) for it in r_items]
            fig_r = go.Figure(data=go.Scatterpolar(r=r_values + [r_values[0]], theta=r_labels + [r_labels[0]], fill='toself'))
            fig_r.update_layout(polar=dict(radialaxis=dict(visible=True, range=[0, 100])), title="생기부 종합 역량 진단", template="plotly")
            fig_r_html = fig_r.to_html(full_html=False, include_plotlyjs=False)
        except: pass

    def md_to_html(text):
        t = text.replace('**', '')
        t = re.sub(r'#### (.*)', r'<strong>\1</strong>', t)
        t = re.sub(r'### (.*)', r'<h3>\1</h3>', t)
        return t.replace('\n', '<br>')

    html = f"""
    <!DOCTYPE html>
    <html lang="ko">
    <head>
        <meta charset="UTF-8">
        <title>대입 컨설팅 리포트 ({target_major})</title>
        <style>
            :root {{ color-scheme: light only !important; }}
            html, body {{
                -webkit-print-color-adjust: exact !important;
                print-color-adjust: exact !important;
                background-color: #ffffff !important;
            }}
            body {{ font-family: 'Malgun Gothic', sans-serif; padding: 40px; color: #111; line-height: 1.8; max-width: 1000px; margin: auto; }}
            h2 {{ text-align: center; border-bottom: 2px solid #333; padding-bottom: 10px; margin-bottom: 30px; }}
            h3 {{ color: #1a73e8; border-left: 5px solid #1a73e8; padding-left: 12px; margin-top: 40px; }}
            .charts {{ display: flex; flex-wrap: wrap; justify-content: space-between; page-break-inside: avoid; margin-bottom: 40px; }}
            .chart {{ width: 48%; margin-bottom: 20px; }}
            @media print {{ body {{ padding: 0; }} .chart {{ page-break-inside: avoid; }} }}
        </style>
    </head>
    <body>
        <h2>🎓 대입 컨설팅 종합 리포트 ({target_major})</h2>
        <div class="charts">
            <div class="chart">{fig_i_html}</div>
            <div class="chart">{fig_m_html}</div>
            <div class="chart">{fig_p_html}</div>
            <div class="chart">{fig_r_html}</div>
        </div>
        <div>
            <h3>📝 [PART 1] 종합 진단</h3><p>{md_to_html(p1)}</p>
            <h3>🎯 [PART 2] 대입 전략 및 보완책</h3><p>{md_to_html(p2)}</p>
            <h3>🚀 [PART 3] 심화 탐구 및 세특 예시</h3><p>{md_to_html(p3)}</p>
            <h3>🎤 [PART 4] 면접 질문</h3><p>{md_to_html(p4)}</p>
        </div>
    </body>
    </html>
    """
    return html

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
# 5. 분석 엔진 (💡 프롬프트 최적화: 상위권 대학 보수적 판정 규칙 추가)
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
            1. **줄글 작성 절대 금지.** 모든 내용은 반드시 글머리 기호('-' 또는 '1.', '2.')를 사용한 개괄식 작성.
            2. 인사말 금지. [PART 1]부터 즉시 시작. 철저한 음슴체(~함, ~임) 사용.
            3. 마지막 두 줄은 반드시 아래 태그여야 함. 반드시 본인 분석 결과와 일치하도록 숫자를 계산할 것.
               @PIE [교과: X, 종합: Y, 정시: Z] @ (합계 100)
               @RADAR [전공적합성: A, 학업역량: B, 진로탐색: C, 리더십/인성: D, 발전가능성: E] @ (0~100)

            [🔥 전형 추천 및 데이터 종합 판단 원칙]
            1. **교과 vs 종합**: 생기부 기록이 빈약하여 본인이 매긴 @RADAR 점수가 낮다면(75점 이하) 종합전형을 1순위로 추천하지 말고 교과전형 비중(X)을 높일 것.
            2. **정시(수능) 전형의 현실적 기준**: 평균 3등급 이내가 아니면 추천 비중(Z)을 0~5%로 극히 낮게 잡을 것. 
            3. **내신 5등급 이하 농어촌 현실**: 농어촌이라 하더라도 5등급 이하의 인서울/지거국 합격은 매우 어렵다는 팩트폭력을 포함할 것.
            4. **상위권 대학(서연고, 서성한, 중경외시) 종합전형 판정**: 지망 대학이 상위권 대학인 경우 종합전형은 매우 보수적으로 판단할 것. 생기부 내용이 완벽하지 않다면 @RADAR의 점수를 엄격하게 부여하고, @PIE에서 종합전형 비중을 낮게 책정할 것.

            [작성 가이드]
            [PART 1] 종합 진단
            - 내신/모의고사 등급 분석 (수치 기반)
            - @RADAR 항목별 장단점 분석.

            [PART 2] 대입 전략, 농어촌 전략, 생기부 보완, 추천 도서
            - 전형별 액션 플랜 (개괄식). '(농어촌)' 표기 삭제.
            - **[농어촌 전형 전략]**: 내신 5등급 이하일 경우 농어촌 조커 활용의 한계를 냉정하게 팩트폭력 할 것.
            - **[생기부 보완 전략]**: 맞춤형 보완책 제시 (미반영 항목 언급 금지).
            - 추천 도서 3권: 아주 짧고 간결한 선정 이유.

            [PART 3] 심화 탐구 및 세특 예시
            - 총 3개 제안. 숫자/세트 번호 금지. 키워드 형식 준수:
            주제: 
            종적/횡적 근거: 
            탐구 방법: 
            세특 예시: 

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

    # 가시성 강화 및 줄바꿈 해결 (💡 면접 질문 파트 가독성 로직 수정)
    p2 = re.sub(r'(?i)농어촌\s*전형\s*전략|농어촌\s*전형\s*유불리\s*판단', '⚖️ **농어촌 전형 전략**', p2)
    p2 = re.sub(r'(?i)생기부\s*보완\s*전략', '🛠️ **생기부 보완 전략**', p2)

    p3 = re.sub(r'(?i)주제\s*:', '\n\n#### 📍 주제:', p3)
    p3 = re.sub(r'(?i)종적/횡적\s*근거\s*:', '\n🔍 **종적/횡적 근거:**', p3)
    p3 = re.sub(r'(?i)탐구\s*방법\s*:', '\n🛠️ **탐구 방법:**', p3)
    p3 = re.sub(r'(?i)세특\s*예시\s*:', '\n✍️ **세특 예시:**', p3)
    
    p4 = re.sub(r'(?i)질문\s*:', '\n#### ❓ 질문:', p4)
    p4 = re.sub(r'(?i)모범\s*답안\s*:', '\n✅ **모범 답안:**\n', p4)
    p4 = re.sub(r'(?i)준비\s*방법\s*:', '\n🛠️ **준비 방법:**\n', p4)

    # --- 차트 렌더링 함수 ---
    def render_all_charts(suffix):
        c1, c2 = st.columns(2); c3, c4 = st.columns(2)
        if not st.session_state.i_df.empty:
            c1.plotly_chart(px.line(st.session_state.i_df, x="학기", y="등급", markers=True, range_y=[9, 1], title="내신 등급 추이"), use_container_width=True, key=f"i_{suffix}")
        if not st.session_state.m_df.empty:
            fig_m = px.line(st.session_state.m_df, x="시험", y=["국어", "수학", "영어", "한국사", "탐구1", "탐구2"], markers=True, range_y=[9, 1], title="모의고사 등급 추이")
            fig_m.update_traces(connectgaps=True)
            c2.plotly_chart(fig_m, use_container_width=True, key=f"m_{suffix}")
        p_match = re.search(r'@PIE\s*\[(.*?)\]\s*@', res, re.IGNORECASE)
        if p_match:
            try:
                p_items = [it.split(':') for it in p_match.group(1).split(',')]
                p_items = [it for it in p_items if len(it) >= 2]
                p_df = pd.DataFrame([{"전형": it[0].strip(), "비중": int(re.sub(r'[^0-9]', '', it[1]) or 0)} for it in p_items])
                c3.plotly_chart(px.pie(p_df, values="비중", names="전형", hole=0.4, title="추천 전형 비율"), use_container_width=True, key=f"p_{suffix}")
            except: pass
        r_match = re.search(r'@RADAR\s*\[(.*?)\]\s*@', res, re.IGNORECASE)
        if r_match:
            try:
                r_items = [it.split(':') for it in r_match.group(1).split(',')]
                r_items = [it for it in r_items if len(it) >= 2]
                r_labels = [it[0].strip() for it in r_items]
                r_values = [int(re.sub(r'[^0-9]', '', it[1]) or 0) for it in r_items]
                fig_r = go.Figure(data=go.Scatterpolar(r=r_values + [r_values[0]], theta=r_labels + [r_labels[0]], fill='toself'))
                fig_r.update_layout(polar=dict(radialaxis=dict(visible=True, range=[0, 100])), title="생기부 종합 역량 진단")
                c4.plotly_chart(fig_r, use_container_width=True, key=f"r_{suffix}")
            except: pass

    # --- 탭 구성 ---
    tab1, tab2, tab3, tab4 = st.tabs(["📊 진단 및 전략", "💡 탐구/면접 가이드", "💬 실시간 상담", "🖨️ 리포트 인쇄"])

    with tab1:
        st.subheader("📊 데이터 기반 컨설팅 대시보드")
        render_all_charts("tab1")
        st.divider()
        st.markdown(f"### 📝 [PART 1] 종합 진단\n\n{p1}")
        st.markdown(f"### 🎯 [PART 2] 대입 전략 및 보완책\n\n{p2}")

    with tab2:
        st.markdown(f"### 🚀 [PART 3] 심화 탐구 및 세특 예시\n\n{p3}")
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
        html_data = create_html_report(target_major, p1, p2, p3, p4, res, st.session_state.i_df, st.session_state.m_df)
        st.download_button(
            label="📄 완벽 인쇄용 HTML 다운로드",
            data=html_data,
            file_name=f"{target_major}_컨설팅_리포트.html",
            mime="text/html",
            use_container_width=True
        )
        st.divider()
        st.markdown(f"## 🎓 대입 컨설팅 종합 리포트 ({target_major})")
        render_all_charts("tab4")
        st.divider()
        st.markdown(f"### 📝 [PART 1] 종합 진단\n\n{p1}\n\n### 🎯 [PART 2] 대입 전략 및 보완책\n\n{p2}\n\n### 🚀 [PART 3] 심화 탐구 및 세특 문구\n\n{p3}\n\n### 🎤 [PART 4] 면접 질문\n\n{p4}")
else:
    st.info("👈 왼쪽 사이드바에 정보를 입력하고 파일을 업로드해 주세요.")
