#!/usr/bin/env python3
"""
Art Asta - Art Portfolio and Auction Platform
=============================================

A comprehensive platform that enables artists to showcase their work,
conduct auctions, and manage custom art requests through an 'art on demand' feature.

Features:
- Artist portfolio showcase
- Live auction system with real-time bidding
- Art on demand custom requests
- Payment processing integration
- User authentication and profiles
- Advanced search and filtering
- Mobile-responsive design

Author: Shivam Sahu
GitHub: https://github.com/shivamsahugzp/art-asta
"""

import os
import sys
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Union
from dataclasses import dataclass, asdict
from pathlib import Path
import asyncio
import json
import uuid

# Flask and web framework imports
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_migrate import Migrate
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# Third-party integrations
import stripe
from PIL import Image
import boto3
from botocore.exceptions import ClientError

# Data processing
import pandas as pd
import numpy as np

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('art_asta.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///art_asta.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Initialize extensions
db = SQLAlchemy(app)
migrate = Migrate(app, db)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'auth.login'
socketio = SocketIO(app, cors_allowed_origins="*")

# Initialize Stripe
stripe.api_key = os.environ.get('STRIPE_SECRET_KEY')

# Initialize AWS S3 (for image storage)
s3_client = boto3.client(
    's3',
    aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
    region_name=os.environ.get('AWS_REGION', 'us-east-1')
)

# Database Models
class User(UserMixin, db.Model):
    """User model for artists and buyers"""
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(120), nullable=False)
    first_name = db.Column(db.String(50), nullable=False)
    last_name = db.Column(db.String(50), nullable=False)
    bio = db.Column(db.Text)
    profile_image = db.Column(db.String(200))
    is_artist = db.Column(db.Boolean, default=False)
    is_verified = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    artworks = db.relationship('Artwork', backref='artist', lazy=True)
    bids = db.relationship('Bid', backref='bidder', lazy=True)
    orders = db.relationship('Order', backref='buyer', lazy=True)
    custom_requests = db.relationship('CustomRequest', backref='requester', lazy=True)
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'email': self.email,
            'first_name': self.first_name,
            'last_name': self.last_name,
            'bio': self.bio,
            'profile_image': self.profile_image,
            'is_artist': self.is_artist,
            'is_verified': self.is_verified,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

class Artwork(db.Model):
    """Artwork model"""
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    medium = db.Column(db.String(100))
    dimensions = db.Column(db.String(100))
    year_created = db.Column(db.Integer)
    price = db.Column(db.Float)
    is_auction = db.Column(db.Boolean, default=False)
    auction_start_price = db.Column(db.Float)
    auction_end_time = db.Column(db.DateTime)
    is_sold = db.Column(db.Boolean, default=False)
    is_available = db.Column(db.Boolean, default=True)
    image_url = db.Column(db.String(500))
    thumbnail_url = db.Column(db.String(500))
    tags = db.Column(db.String(500))  # JSON string of tags
    views_count = db.Column(db.Integer, default=0)
    likes_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Foreign keys
    artist_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Relationships
    bids = db.relationship('Bid', backref='artwork', lazy=True)
    order_items = db.relationship('OrderItem', backref='artwork', lazy=True)
    
    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'description': self.description,
            'medium': self.medium,
            'dimensions': self.dimensions,
            'year_created': self.year_created,
            'price': self.price,
            'is_auction': self.is_auction,
            'auction_start_price': self.auction_start_price,
            'auction_end_time': self.auction_end_time.isoformat() if self.auction_end_time else None,
            'is_sold': self.is_sold,
            'is_available': self.is_available,
            'image_url': self.image_url,
            'thumbnail_url': self.thumbnail_url,
            'tags': json.loads(self.tags) if self.tags else [],
            'views_count': self.views_count,
            'likes_count': self.likes_count,
            'artist': self.artist.to_dict() if self.artist else None,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

class Bid(db.Model):
    """Bid model for auctions"""
    id = db.Column(db.Integer, primary_key=True)
    amount = db.Column(db.Float, nullable=False)
    is_winning = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Foreign keys
    artwork_id = db.Column(db.Integer, db.ForeignKey('artwork.id'), nullable=False)
    bidder_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    def to_dict(self):
        return {
            'id': self.id,
            'amount': self.amount,
            'is_winning': self.is_winning,
            'bidder': self.bidder.to_dict() if self.bidder else None,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

class CustomRequest(db.Model):
    """Custom art request model"""
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    budget_range = db.Column(db.String(100))
    deadline = db.Column(db.DateTime)
    reference_images = db.Column(db.Text)  # JSON string of image URLs
    status = db.Column(db.String(50), default='pending')  # pending, accepted, in_progress, completed, cancelled
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Foreign keys
    requester_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    assigned_artist_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    
    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'description': self.description,
            'budget_range': self.budget_range,
            'deadline': self.deadline.isoformat() if self.deadline else None,
            'reference_images': json.loads(self.reference_images) if self.reference_images else [],
            'status': self.status,
            'requester': self.requester.to_dict() if self.requester else None,
            'assigned_artist': self.assigned_artist.to_dict() if self.assigned_artist else None,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

class Order(db.Model):
    """Order model for purchases"""
    id = db.Column(db.Integer, primary_key=True)
    total_amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(50), default='pending')  # pending, paid, shipped, delivered, cancelled
    payment_intent_id = db.Column(db.String(200))
    shipping_address = db.Column(db.Text)  # JSON string
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Foreign keys
    buyer_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Relationships
    order_items = db.relationship('OrderItem', backref='order', lazy=True)
    
    def to_dict(self):
        return {
            'id': self.id,
            'total_amount': self.total_amount,
            'status': self.status,
            'payment_intent_id': self.payment_intent_id,
            'shipping_address': json.loads(self.shipping_address) if self.shipping_address else {},
            'buyer': self.buyer.to_dict() if self.buyer else None,
            'order_items': [item.to_dict() for item in self.order_items],
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

class OrderItem(db.Model):
    """Order item model"""
    id = db.Column(db.Integer, primary_key=True)
    quantity = db.Column(db.Integer, default=1)
    price = db.Column(db.Float, nullable=False)
    
    # Foreign keys
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False)
    artwork_id = db.Column(db.Integer, db.ForeignKey('artwork.id'), nullable=False)
    
    def to_dict(self):
        return {
            'id': self.id,
            'quantity': self.quantity,
            'price': self.price,
            'artwork': self.artwork.to_dict() if self.artwork else None
        }

# Utility Classes
class ImageProcessor:
    """Handle image processing and uploads"""
    
    @staticmethod
    def allowed_file(filename):
        ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
        return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS
    
    @staticmethod
    def process_image(file, artwork_id):
        """Process and upload image to S3"""
        try:
            # Generate unique filename
            filename = secure_filename(file.filename)
            unique_filename = f"{artwork_id}_{uuid.uuid4().hex}_{filename}"
            
            # Process image
            img = Image.open(file)
            
            # Create thumbnail
            img.thumbnail((300, 300), Image.Resampling.LANCZOS)
            thumbnail_filename = f"thumb_{unique_filename}"
            
            # Upload to S3
            bucket_name = os.environ.get('S3_BUCKET_NAME', 'art-asta-uploads')
            
            # Upload original
            file.seek(0)
            s3_client.upload_fileobj(
                file, 
                bucket_name, 
                f"artworks/{unique_filename}",
                ExtraArgs={'ContentType': file.content_type}
            )
            
            # Upload thumbnail
            thumbnail_buffer = io.BytesIO()
            img.save(thumbnail_buffer, format='JPEG', quality=85)
            thumbnail_buffer.seek(0)
            
            s3_client.upload_fileobj(
                thumbnail_buffer,
                bucket_name,
                f"thumbnails/{thumbnail_filename}",
                ExtraArgs={'ContentType': 'image/jpeg'}
            )
            
            return {
                'image_url': f"https://{bucket_name}.s3.amazonaws.com/artworks/{unique_filename}",
                'thumbnail_url': f"https://{bucket_name}.s3.amazonaws.com/thumbnails/{thumbnail_filename}"
            }
            
        except Exception as e:
            logger.error(f"Image processing error: {e}")
            return None

class AuctionManager:
    """Manage auction functionality"""
    
    @staticmethod
    def get_active_auctions():
        """Get all active auctions"""
        return Artwork.query.filter(
            Artwork.is_auction == True,
            Artwork.auction_end_time > datetime.utcnow(),
            Artwork.is_sold == False
        ).all()
    
    @staticmethod
    def get_auction_bids(artwork_id):
        """Get all bids for an artwork"""
        return Bid.query.filter_by(artwork_id=artwork_id).order_by(Bid.amount.desc()).all()
    
    @staticmethod
    def place_bid(artwork_id, bidder_id, amount):
        """Place a bid on an artwork"""
        artwork = Artwork.query.get(artwork_id)
        if not artwork or not artwork.is_auction:
            return False, "Artwork not found or not available for auction"
        
        if artwork.auction_end_time < datetime.utcnow():
            return False, "Auction has ended"
        
        # Check if bid is higher than current highest
        highest_bid = Bid.query.filter_by(artwork_id=artwork_id).order_by(Bid.amount.desc()).first()
        if highest_bid and amount <= highest_bid.amount:
            return False, "Bid must be higher than current highest bid"
        
        # Create new bid
        bid = Bid(
            artwork_id=artwork_id,
            bidder_id=bidder_id,
            amount=amount
        )
        
        # Mark previous highest bid as not winning
        if highest_bid:
            highest_bid.is_winning = False
        
        bid.is_winning = True
        db.session.add(bid)
        db.session.commit()
        
        # Emit real-time update
        socketio.emit('bid_placed', {
            'artwork_id': artwork_id,
            'bid': bid.to_dict()
        }, room=f'auction_{artwork_id}')
        
        return True, "Bid placed successfully"
    
    @staticmethod
    def end_auction(artwork_id):
        """End an auction and determine winner"""
        artwork = Artwork.query.get(artwork_id)
        if not artwork:
            return False, "Artwork not found"
        
        winning_bid = Bid.query.filter_by(
            artwork_id=artwork_id,
            is_winning=True
        ).first()
        
        if winning_bid:
            artwork.is_sold = True
            artwork.price = winning_bid.amount
            db.session.commit()
            
            # Emit auction ended event
            socketio.emit('auction_ended', {
                'artwork_id': artwork_id,
                'winner': winning_bid.bidder.to_dict(),
                'winning_amount': winning_bid.amount
            }, room=f'auction_{artwork_id}')
            
            return True, "Auction ended successfully"
        
        return False, "No winning bid found"

# Routes
@app.route('/')
def index():
    """Home page"""
    featured_artworks = Artwork.query.filter_by(is_available=True).order_by(Artwork.created_at.desc()).limit(8).all()
    active_auctions = AuctionManager.get_active_auctions()[:4]
    
    return render_template('index.html', 
                         featured_artworks=featured_artworks,
                         active_auctions=active_auctions)

@app.route('/artworks')
def artworks():
    """Artworks gallery page"""
    page = request.args.get('page', 1, type=int)
    per_page = 12
    
    # Filtering
    medium = request.args.get('medium')
    price_min = request.args.get('price_min', type=float)
    price_max = request.args.get('price_max', type=float)
    search = request.args.get('search')
    
    query = Artwork.query.filter_by(is_available=True)
    
    if medium:
        query = query.filter(Artwork.medium.ilike(f'%{medium}%'))
    
    if price_min:
        query = query.filter(Artwork.price >= price_min)
    
    if price_max:
        query = query.filter(Artwork.price <= price_max)
    
    if search:
        query = query.filter(
            db.or_(
                Artwork.title.ilike(f'%{search}%'),
                Artwork.description.ilike(f'%{search}%'),
                Artwork.tags.ilike(f'%{search}%')
            )
        )
    
    artworks = query.order_by(Artwork.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    
    return render_template('artworks.html', artworks=artworks)

@app.route('/artwork/<int:artwork_id>')
def artwork_detail(artwork_id):
    """Artwork detail page"""
    artwork = Artwork.query.get_or_404(artwork_id)
    
    # Increment view count
    artwork.views_count += 1
    db.session.commit()
    
    # Get bids if it's an auction
    bids = []
    if artwork.is_auction:
        bids = AuctionManager.get_auction_bids(artwork_id)
    
    return render_template('artwork_detail.html', artwork=artwork, bids=bids)

@app.route('/auctions')
def auctions():
    """Active auctions page"""
    active_auctions = AuctionManager.get_active_auctions()
    return render_template('auctions.html', auctions=active_auctions)

@app.route('/artists')
def artists():
    """Artists page"""
    artists = User.query.filter_by(is_artist=True, is_verified=True).all()
    return render_template('artists.html', artists=artists)

@app.route('/artist/<int:artist_id>')
def artist_profile(artist_id):
    """Artist profile page"""
    artist = User.query.get_or_404(artist_id)
    artworks = Artwork.query.filter_by(artist_id=artist_id, is_available=True).all()
    return render_template('artist_profile.html', artist=artist, artworks=artworks)

@app.route('/custom-request', methods=['GET', 'POST'])
@login_required
def custom_request():
    """Custom art request page"""
    if request.method == 'POST':
        title = request.form.get('title')
        description = request.form.get('description')
        budget_range = request.form.get('budget_range')
        deadline_str = request.form.get('deadline')
        
        deadline = None
        if deadline_str:
            deadline = datetime.strptime(deadline_str, '%Y-%m-%d')
        
        custom_request = CustomRequest(
            title=title,
            description=description,
            budget_range=budget_range,
            deadline=deadline,
            requester_id=current_user.id
        )
        
        db.session.add(custom_request)
        db.session.commit()
        
        flash('Custom request submitted successfully!', 'success')
        return redirect(url_for('custom_request'))
    
    return render_template('custom_request.html')

# API Routes
@app.route('/api/artworks')
def api_artworks():
    """API endpoint for artworks"""
    artworks = Artwork.query.filter_by(is_available=True).all()
    return jsonify([artwork.to_dict() for artwork in artworks])

@app.route('/api/auctions')
def api_auctions():
    """API endpoint for active auctions"""
    auctions = AuctionManager.get_active_auctions()
    return jsonify([auction.to_dict() for auction in auctions])

@app.route('/api/bid', methods=['POST'])
@login_required
def api_place_bid():
    """API endpoint to place a bid"""
    data = request.get_json()
    artwork_id = data.get('artwork_id')
    amount = data.get('amount')
    
    success, message = AuctionManager.place_bid(artwork_id, current_user.id, amount)
    
    return jsonify({'success': success, 'message': message})

# Socket.IO Events
@socketio.on('join_auction')
def on_join_auction(data):
    """Join auction room for real-time updates"""
    artwork_id = data['artwork_id']
    join_room(f'auction_{artwork_id}')
    emit('status', {'msg': f'Joined auction {artwork_id}'})

@socketio.on('leave_auction')
def on_leave_auction(data):
    """Leave auction room"""
    artwork_id = data['artwork_id']
    leave_room(f'auction_{artwork_id}')
    emit('status', {'msg': f'Left auction {artwork_id}'})

# User loader for Flask-Login
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Error handlers
@app.errorhandler(404)
def not_found_error(error):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return render_template('500.html'), 500

def create_tables():
    """Create database tables"""
    with app.app_context():
        db.create_all()
        logger.info("Database tables created successfully")

def main():
    """Main entry point"""
    # Create upload directory
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    
    # Create database tables
    create_tables()
    
    # Run the application
    logger.info("Starting Art Asta application...")
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)

if __name__ == '__main__':
    main()
