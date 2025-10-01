import asyncio
import logging
import os
from datetime import datetime
import pandas as pd
from io import StringIO
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from telegram.error import BadRequest
from pygrowup import Calculator
from models import User, Child, Measurement
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# States for ConversationHandler
REGISTER_CAREGIVER, REGISTER_CHILD_NAME, REGISTER_AGE, REGISTER_SEX, INPUT_WEIGHT, INPUT_HEIGHT, INPUT_MUAC = range(7)

# DB Setup
engine = create_engine('sqlite:///nutricare.db')
Session = sessionmaker(bind=engine)

# WHO Recommendations (simplified from 2023 guideline)
RECOMMENDATIONS = {
    'SAM': "🚨 **Severe Acute Malnutrition (SAM)**: Urgent! Refer to nearest health facility immediately for medical assessment and RUTF treatment. Continue breastfeeding. Monitor for oedema or complications.",
    'MAM': "⚠️ **Moderate Acute Malnutrition (MAM)**: Provide supplementary feeding (RUSF if available). Enhance diet with nutrient-rich foods. Follow up in 2 weeks. Promote hygiene and breastfeeding.",
    'NORMAL': "✅ **Normal Status**: Excellent! Continue exclusive breastfeeding (0-6 mo) or balanced complementary feeding. Ensure play, vaccination, and regular check-ups."
}

# Disclaimer
DISCLAIMER = "\n\n*Disclaimer: This bot provides informational guidance based on WHO standards. It is not a substitute for professional medical advice. Always consult a healthcare provider.*"

# Helper to send messages safely
async def send_message(update: Update, text: str, reply_markup=None, parse_mode=None):
    query = update.callback_query if update.callback_query else None
    try:
        if query:
            try:
                await query.answer()  # Attempt to answer the query
            except BadRequest as e:
                logger.warning(f"Failed to answer callback query: {e}")
                # Continue without answering if query is invalid or too old
        if query:
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        elif update.effective_message:
            await update.effective_message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        else:
            logger.error("No valid message or query to send response")
    except Exception as e:
        logger.error(f"Error in send_message: {e}")

def get_status(muac, bmi_z, age_months):
    if age_months < 6:
        if bmi_z is not None and bmi_z <= -3:
            return 'SAM'
        elif bmi_z is not None and -3 < bmi_z < -2:
            return 'MAM'
        else:
            return 'NORMAL'
    else:
        muac_status = 'SAM' if muac < 115 else 'MAM' if muac < 125 else 'NORMAL'
        bmi_status = 'SAM' if bmi_z is not None and bmi_z <= -3 else 'MAM' if bmi_z is not None and bmi_z < -2 else 'NORMAL'
        if muac_status == 'SAM' or bmi_status == 'SAM':
            return 'SAM'
        elif muac_status == 'MAM' or bmi_status == 'MAM':
            return 'MAM'
        else:
            return 'NORMAL'

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=str(user_id)).first()
        keyboard = [
            [InlineKeyboardButton("📝 Register / Add Child", callback_data="register")],
            [InlineKeyboardButton("👨‍👩‍👧‍👦 View My Children", callback_data="view_children")],
            [InlineKeyboardButton("📊 Add Measurement", callback_data="add_meas")],
            [InlineKeyboardButton("📈 Summarize Data", callback_data="summarize")],
            [InlineKeyboardButton("📤 Export CSV", callback_data="export")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        welcome = "🌟 **Welcome to NutriCare Bot!** 🌟\n\nProfessional nutrition monitoring for children 0-5 years per WHO guidelines. Get personalized recommendations based on MUAC & BMI." + DISCLAIMER
        if not user:
            welcome += "\n\nLet's get started by registering! 👇"
        await send_message(update, welcome, reply_markup=reply_markup, parse_mode='Markdown')
        return ConversationHandler.END
    finally:
        session.close()

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    user_id = update.effective_user.id
    data = query.data if query else None
    
    session = Session()
    try:
        if data == "register":
            await send_message(update, "👤 **Step 1/4**: Enter your name (caregiver):", parse_mode='Markdown')
            context.user_data['state'] = 'register_caregiver'
            return REGISTER_CAREGIVER
        
        elif data == "view_children":
            user = session.query(User).filter_by(telegram_id=str(user_id)).first()
            if not user:
                await send_message(update, "No account found. Please register first! 👆" + DISCLAIMER, parse_mode='Markdown')
                return ConversationHandler.END
            keyboard = []
            for child in user.children:
                keyboard.append([InlineKeyboardButton(f"👶 {child.child_name} ({child.age_months} mo, {child.sex})", callback_data=f"select_child_{child.id}")])
            keyboard.append([InlineKeyboardButton("🔙 Back to Main", callback_data="back_main")])
            children_text = "**Your Children:**\n" + "\n".join([f"• {c.child_name} ({c.age_months} mo, {c.sex})" for c in user.children]) or "No children registered yet. Add one! 📝"
            await send_message(update, children_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
            return ConversationHandler.END
        
        elif data.startswith("select_child_"):
            try:
                child_id = int(data.split("_")[2])
                child = session.get(Child, child_id)
                if not child or child.user.telegram_id != str(user_id):
                    await send_message(update, "⚠️ Child not found or does not belong to you. Please select again." + DISCLAIMER, parse_mode='Markdown')
                    return ConversationHandler.END
                context.user_data['child_id'] = child_id
                await send_message(
                    update,
                    f"✅ Selected child: {child.child_name}. What next?",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="back_main")]]),
                    parse_mode='Markdown'
                )
            except (IndexError, ValueError):
                await send_message(update, "⚠️ Invalid child selection. Please try again." + DISCLAIMER, parse_mode='Markdown')
            return ConversationHandler.END
        
        elif data in ["add_meas", "summarize", "export"]:
            user = session.query(User).filter_by(telegram_id=str(user_id)).first()
            if not user or not user.children:
                await send_message(update, "Register at least one child first! 📝" + DISCLAIMER, parse_mode='Markdown')
                return ConversationHandler.END
            if len(user.children) == 1:
                context.user_data['child_id'] = user.children[0].id
                return await handle_action(update, context, data)
            else:
                keyboard = []
                for child in user.children:
                    keyboard.append([InlineKeyboardButton(f"👶 {child.child_name}", callback_data=f"{data}_child_{child.id}")])
                keyboard.append([InlineKeyboardButton("🔙 Cancel", callback_data="back_main")])
                await send_message(
                    update,
                    f"Select a child for {data.replace('_', ' ').title()}:",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='Markdown'
                )
                return ConversationHandler.END
        
        elif data.startswith("add_meas_child_") or data.startswith("summarize_child_") or data.startswith("export_child_"):
            parts = data.split("_")
            if len(parts) != 4:
                await send_message(update, "⚠️ Invalid action data. Please try again." + DISCLAIMER, parse_mode='Markdown')
                return ConversationHandler.END
            try:
                action = parts[0]
                child_id = int(parts[3])
                child = session.get(Child, child_id)
                if not child or child.user.telegram_id != str(user_id):
                    await send_message(update, "⚠️ Child not found or does not belong to you. Please select again." + DISCLAIMER, parse_mode='Markdown')
                    return ConversationHandler.END
                context.user_data['child_id'] = child_id
                return await handle_action(update, context, action)
            except ValueError:
                logger.error(f"Invalid child_id in data: {data}")
                await send_message(update, "⚠️ Invalid child ID. Please select a valid child." + DISCLAIMER, parse_mode='Markdown')
                return ConversationHandler.END
            except Exception as e:
                logger.error(f"Error processing action {data}: {e}")
                await send_message(update, "⚠️ Error processing request. Please try again." + DISCLAIMER, parse_mode='Markdown')
                return ConversationHandler.END
        
        elif data == "add_another_child":
            await send_message(update, "👶 **New Child - Step 1/3**: Enter child name:", parse_mode='Markdown')
            return REGISTER_CHILD_NAME
        
        elif data == "back_main":
            await start(update, context)
            return ConversationHandler.END
        
        else:
            await send_message(update, "⚠️ Invalid action. Please try again." + DISCLAIMER, parse_mode='Markdown')
            return ConversationHandler.END

    except Exception as e:
        logger.error(f"Error in button_handler: {e}")
        await send_message(update, "⚠️ An unexpected error occurred. Please try again or contact support." + DISCLAIMER, parse_mode='Markdown')
        return ConversationHandler.END
    finally:
        session.close()

async def handle_action(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str) -> int:
    if action == "add_meas":
        await send_message(update, "⚖️ **Step 1/3**: Enter weight (kg, e.g., 8.5):", parse_mode='Markdown')
        return INPUT_WEIGHT
    elif action == "summarize":
        session = Session()
        try:
            child = session.get(Child, context.user_data['child_id'])
            if not child:
                await send_message(update, "Child not found. Try again." + DISCLAIMER, parse_mode='Markdown')
                return ConversationHandler.END
            meas = child.measurements[-5:] if child.measurements else []
            summary = f"**📈 Summary for {child.child_name}** 🌟\nAge: {child.age_months} months\nSex: {child.sex.capitalize()}\n\n"
            if not meas:
                summary += "No measurements yet. Add one! 📊"
            else:
                for m in meas:
                    summary += f"📅 {m.date.strftime('%Y-%m-%d')}: **{m.status}** (BMI Z: {m.bmi_z:.2f if m.bmi_z else 'N/A'} | Weight: {m.weight}kg | Height: {m.height}cm | MUAC: {m.muac or 'N/A'}mm)\n"
                summary += f"\n**Latest Status:** {meas[-1].status}\n\n{RECOMMENDATIONS.get(meas[-1].status, 'Add data!')}"
            keyboard = [[InlineKeyboardButton("🏠 Main Menu", callback_data="back_main")]]
            await send_message(update, summary + DISCLAIMER, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Error in summarize: {e}")
            await send_message(update, "⚠️ Error fetching summary. Try again." + DISCLAIMER, parse_mode='Markdown')
        finally:
            session.close()
        return ConversationHandler.END
    elif action == "export":
        session = Session()
        try:
            child = session.get(Child, context.user_data['child_id'])
            if not child or not child.measurements:
                await send_message(update, "No data to export for this child." + DISCLAIMER, parse_mode='Markdown')
                return ConversationHandler.END
            df = pd.DataFrame([
                {
                    'Date': m.date.strftime('%Y-%m-%d'),
                    'Weight (kg)': m.weight,
                    'Height (cm)': m.height,
                    'MUAC (mm)': m.muac,
                    'BMI Z-Score': m.bmi_z,
                    'Status': m.status
                } for m in child.measurements
            ])
            csv_buffer = StringIO()
            df.to_csv(csv_buffer, index=False)
            csv_buffer.seek(0)
            if update.effective_message:
                await update.effective_message.reply_document(
                    document=csv_buffer.getvalue().encode(),
                    filename=f"{child.child_name}_nutricare.csv",
                    caption="📤 Your child's nutrition data exported as CSV! 🌟"
                )
            await send_message(update, "✅ Data exported successfully!" + DISCLAIMER, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Error in export: {e}")
            await send_message(update, "⚠️ Error exporting data. Try again." + DISCLAIMER, parse_mode='Markdown')
        finally:
            session.close()
        return ConversationHandler.END

async def register_caregiver(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['caregiver_name'] = update.message.text.strip()
    if not context.user_data['caregiver_name']:
        await send_message(update, "Invalid name. Please enter a valid name:", parse_mode='Markdown')
        return REGISTER_CAREGIVER
    await send_message(update, "👶 **Step 2/4**: Enter child name:", parse_mode='Markdown')
    return REGISTER_CHILD_NAME

async def register_child_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['child_name'] = update.message.text.strip()
    if not context.user_data['child_name']:
        await send_message(update, "Invalid name. Enter child name:", parse_mode='Markdown')
        return REGISTER_CHILD_NAME
    await send_message(update, "📅 **Step 3/4**: Enter age in months (0-60):", parse_mode='Markdown')
    return REGISTER_AGE

async def register_age(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        age = int(update.message.text)
        if not 0 <= age <= 60:
            raise ValueError("Age must be between 0 and 60 months.")
        context.user_data['age_months'] = age
    except ValueError as e:
        await send_message(update, f"⚠️ {str(e)} Please enter a valid number (0-60):", parse_mode='Markdown')
        return REGISTER_AGE
    keyboard = [
        [InlineKeyboardButton("👦 Male", callback_data="sex_male")],
        [InlineKeyboardButton("👧 Female", callback_data="sex_female")]
    ]
    await send_message(update, "**Step 4/4**: Select sex:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return REGISTER_SEX

async def register_sex(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        if query:
            await query.answer()
    except BadRequest as e:
        logger.warning(f"Failed to answer callback query in register_sex: {e}")
    
    sex = 'male' if query.data == 'sex_male' else 'female'
    context.user_data['sex'] = sex
    
    session = Session()
    try:
        user_id = str(update.effective_user.id)
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            user = User(telegram_id=user_id, caregiver_name=context.user_data.get('caregiver_name', 'Unknown'))
            session.add(user)
            session.commit()
        
        child = Child(
            user_id=user.id,
            child_name=context.user_data['child_name'],
            age_months=context.user_data['age_months'],
            sex=sex
        )
        session.add(child)
        session.commit()
        
        keyboard = [
            [InlineKeyboardButton("➕ Add Another Child", callback_data="add_another_child")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="back_main")]
        ]
        await send_message(
            update,
            f"✅ **Child Registered Successfully!** 🎉\nCaregiver: {user.caregiver_name}\nChild: {child.child_name} ({sex.capitalize()}, {child.age_months} months)\n\nStart monitoring now!" + DISCLAIMER,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Error in registration: {e}")
        await send_message(update, "⚠️ Error registering child. Please try again." + DISCLAIMER, parse_mode='Markdown')
    finally:
        session.close()
        context.user_data.clear()
    return ConversationHandler.END

async def input_weight(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        weight = float(update.message.text)
        if weight <= 0 or weight > 50:
            raise ValueError("Weight must be positive and reasonable (e.g., 0.5-30 kg).")
        context.user_data['weight'] = weight
    except ValueError as e:
        await send_message(update, f"⚠️ {str(e)} Enter valid weight (kg):", parse_mode='Markdown')
        return INPUT_WEIGHT
    await send_message(update, "📏 **Step 2/3**: Enter height/length (cm, e.g., 70.5):", parse_mode='Markdown')
    return INPUT_HEIGHT

async def input_height(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        height = float(update.message.text)
        if height <= 0 or height > 150:
            raise ValueError("Height must be positive and reasonable (e.g., 45-120 cm).")
        context.user_data['height'] = height
    except ValueError as e:
        await send_message(update, f"⚠️ {str(e)} Enter valid height (cm):", parse_mode='Markdown')
        return INPUT_HEIGHT
    
    session = Session()
    try:
        child = session.get(Child, context.user_data['child_id'])
        if not child:
            await send_message(update, "Child not found. Please select a child again." + DISCLAIMER, parse_mode='Markdown')
            return ConversationHandler.END
        if child.age_months >= 6:
            await send_message(update, "📐 **Step 3/3**: Enter MUAC (mm, e.g., 120):", parse_mode='Markdown')
            return INPUT_MUAC
        else:
            await calculate_and_save(update, context, muac=None)
            return ConversationHandler.END
    finally:
        session.close()

async def input_muac(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        muac = float(update.message.text)
        if muac <= 0 or muac > 300:
            raise ValueError("MUAC must be positive and reasonable (e.g., 80-200 mm).")
        context.user_data['muac'] = muac
    except ValueError as e:
        await send_message(update, f"⚠️ {str(e)} Enter valid MUAC (mm):", parse_mode='Markdown')
        return INPUT_MUAC
    await calculate_and_save(update, context, muac=muac)
    return ConversationHandler.END

async def calculate_and_save(update: Update, context: ContextTypes.DEFAULT_TYPE, muac=None):
    session = Session()
    try:
        child = session.get(Child, context.user_data['child_id'])
        if not child:
            await send_message(update, "Child not found. Please select a child again." + DISCLAIMER, parse_mode='Markdown')
            return
        sex = child.sex
        age_months = child.age_months
        weight = context.user_data['weight']
        height = context.user_data['height']
        
        bmi_z = None
        try:
            bmi_value = weight / ((height / 100) ** 2)
            calculator = Calculator(adjust_height_data=False, adjust_weight_data=False)
            age_days = age_months * 30.42
            bmi_z = calculator.bmifa(bmi_value, age_days, sex)
        except Exception as e:
            logger.warning(f"pygrowup error: {e}. Falling back to MUAC-only or raw BMI.")
            bmi_z = None
        
        status = get_status(muac, bmi_z, age_months)
        
        meas = Measurement(
            child_id=child.id,
            weight=weight,
            height=height,
            muac=muac,
            bmi_z=bmi_z,
            status=status
        )
        session.add(meas)
        session.commit()
        
        rec = RECOMMENDATIONS[status]
        result_text = f"📊 **Results for {child.child_name}:** 🎉\nWeight: {weight} kg\nHeight: {height} cm\nMUAC: {muac or 'N/A'} mm\nBMI Z-Score: {bmi_z:.2f if bmi_z is not None else 'N/A'}\n\n**Status: {status}**\n\n{rec}" + DISCLAIMER
        keyboard = [[InlineKeyboardButton("🏠 Main Menu", callback_data="back_main")]]
        await send_message(update, result_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in calculate_and_save: {e}")
        await send_message(update, "⚠️ Error saving measurement. Please try again." + DISCLAIMER, parse_mode='Markdown')
    finally:
        session.close()
        context.user_data.clear()

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Exception while handling an update: {context.error}")
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ An unexpected error occurred. We're on it! Try again later." + DISCLAIMER,
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Failed to send error message: {e}")

def main():
    token = os.getenv('TELEGRAM_TOKEN')
    if not token:
        logger.error("TELEGRAM_TOKEN not set!")
        raise ValueError("TELEGRAM_TOKEN environment variable is missing.")
    app = Application.builder().token(token).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start), CallbackQueryHandler(button_handler)],
        states={
            REGISTER_CAREGIVER: [MessageHandler(filters.TEXT & ~filters.COMMAND, register_caregiver)],
            REGISTER_CHILD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, register_child_name)],
            REGISTER_AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, register_age)],
            REGISTER_SEX: [CallbackQueryHandler(register_sex, pattern="^sex_")],
            INPUT_WEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_weight)],
            INPUT_HEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_height)],
            INPUT_MUAC: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_muac)],
        },
        fallbacks=[CallbackQueryHandler(button_handler, pattern="^add_another_child$|^back_main$")],
        per_message=False
    )
    
    app.add_handler(conv_handler)
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_error_handler(error_handler)
    
    # For local testing, use polling with increased timeout
    # app.run_polling(timeout=60)
    
    # For Render deployment, use webhook
    port = int(os.environ.get('PORT', 8443))
    webhook_url = os.getenv('WEBHOOK_URL')
    if not webhook_url:
        logger.error("WEBHOOK_URL not set!")
        raise ValueError("WEBHOOK_URL environment variable is missing.")
    app.run_webhook(
        listen='0.0.0.0',
        port=port,
        url_path='/webhook',
        webhook_url=webhook_url + '/webhook'
    )

if __name__ == '__main__':
    main()