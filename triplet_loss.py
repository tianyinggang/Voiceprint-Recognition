import logging
import constants as c
import numpy as np
import math
import tensorflow as tf
from tensorflow import keras
from tensorflow.keras import backend as K
import random
'''
https://arxiv.org/pdf/1801.07698.pdf
https://github.com/deepinsight/insightface
https://github.com/auroua/InsightFace_TF/blob/master/losses/face_losses.py

'''

alpha = c.ALPHA  # used in FaceNet https://arxiv.org/pdf/1503.03832.pdf


def batch_cosine_similarity(x1, x2):
    # https://en.wikipedia.org/wiki/Cosine_similarity
    # 1 = equal direction ; -1 = opposite direction
    dot = K.squeeze(K.batch_dot(x1, x2, axes=1), axis=1)
    return dot

def center_loss(num_classes:int):

    def center_loss_(labels, features):
        """
        获取center loss及更新样本的center
        :param labels: Tensor,表征样本label,非one-hot编码,shape应为(batch_size,).
        :param features: Tensor,表征样本特征,最后一个fc层的输出,shape应该为(batch_size, num_classes).
        :param alpha: 0-1之间的数字,控制样本类别中心的学习率,细节参考原文.
        :param num_classes: 整数,表明总共有多少个类别,网络分类输出有多少个神经元这里就取多少.
        :return: Tensor, center-loss， shape因为(batch_size,)
        """
        #根据网络的输出神经元数量
        # 更新中心的学习率
        alpha = 0.6
        
        # 获取特征的维数，例如256维
        len_features = features.get_shape()[1]
        # 建立一个Variable,shape为[num_classes, len_features]，用于存储整个网络的样本中心，
        # 设置trainable=False是因为样本中心不是由梯度进行更新的
        centers = tf.compat.v1.get_variable('centers', [num_classes, len_features], dtype=tf.float32,
                                initializer=tf.compat.v1.constant_initializer(0), trainable=False)
        # 将label展开为一维的，如果labels已经是一维的，则该动作其实无必要
        labels = tf.reshape(labels, [-1])

        # 根据样本label,获取mini-batch中每一个样本对应的中心值
        centers_batch = tf.gather(centers, tf.cast(labels, tf.int32))

        # 当前mini-batch的特征值与它们对应的中心值之间的差
        diff = centers_batch - features

        # 获取mini-batch中同一类别样本出现的次数,了解原理请参考原文公式(4)
        unique_label, unique_idx, unique_count = tf.unique_with_counts(labels)
        appear_times = tf.gather(unique_count, unique_idx)
        appear_times = tf.reshape(appear_times, [-1, 1])

        diff = diff / tf.cast((1 + appear_times), tf.float32)
        diff = alpha * diff

        # 更新centers
        centers_update_op = tf.compat.v1.scatter_sub(centers, tf.cast(labels, tf.int32), diff)

        # 这里使用tf.control_dependencies更新centers
        with tf.compat.v1.control_dependencies([centers_update_op]):
            # 计算center-loss
            c_loss = tf.nn.l2_loss(features - centers_batch)

        return c_loss
    return center_loss_



def coco_loss(out_num:int):
    # Using Keras GlorotUniform initializer instead of tf.contrib.layers.xavier_initializer
    w_init = tf.keras.initializers.GlorotUniform()
    s = 30
    m = 0.4
    def cosineface_losses(y_true, y_pred):
        y_true = tf.cast(y_true, tf.int32)
        with tf.compat.v1.variable_scope('coco_losss'):
            y_pred_norm = tf.norm(y_pred, axis=1, keepdims=True)
            y_pred = tf.div(y_pred, y_pred_norm, name='norm_ypred')

            weights = tf.compat.v1.get_variable(name='embedding_weights', shape=(y_pred.shape[-1], out_num),
                                    initializer=w_init, dtype=tf.float32)
            weights_norm = tf.norm(weights, axis=0, keepdims=True)
            weights = tf.div(weights, weights_norm, name='norm_weights')
            
            # cos_theta - m
            cos_t = tf.matmul(y_pred, weights, name='cos_t')
            cos_t_m = tf.subtract(cos_t, m, name='cos_t_m')
            
            mask = tf.one_hot(y_true, depth=out_num, name='one_hot_mask')
            inv_mask = tf.subtract(1., mask, name='inverse_mask')
            
            output = tf.add(s * tf.multiply(cos_t, inv_mask), s * tf.multiply(cos_t_m, mask), name='coco_loss_output')
        
        return output

    return cosineface_losses


def softmax_loss(out_num: int):
    """
    使用稀疏交叉熵：y_true 维度 (batch,), y_pred 维度 (batch, out_num)。
    """
    # from_logits=False 表示 y_pred 已经过 softmax；若输出是 logits，请设为 True
    loss_fn = tf.keras.losses.SparseCategoricalCrossentropy(from_logits=False,
                                                            reduction=tf.keras.losses.Reduction.NONE)

    def softmax_loss_(y_true, y_pred):
        # 直接返回每个样本的交叉熵损失 (batch,)
        return loss_fn(y_true, y_pred)

    return softmax_loss_


def AAM_loss(out_num: int):
    w_init = tf.keras.initializers.GlorotUniform()
    s, m = 64, 0.5

    def additive_angular_margin_loss(y_true, y_pred):
        y_true = tf.cast(y_true, tf.int32)
        cos_m = math.cos(m)
        sin_m = math.sin(m)
        mm = sin_m * m  # issue 1
        threshold = math.cos(math.pi - m)

        with tf.compat.v1.variable_scope('aam_loss'):
            # inputs and weights norm
            y_pred_norm = tf.norm(y_pred, axis=1, keepdims=True)
            y_pred = tf.div(y_pred, y_pred_norm, name='norm_y_pred')
            weights = tf.compat.v1.get_variable(name='embedding_weights', shape=(y_pred.shape[-1], out_num),
                                    initializer=w_init, dtype=tf.float32)
            weights_norm = tf.norm(weights, axis=0, keepdims=True)
            weights = tf.div(weights, weights_norm, name='norm_weights')
            # cos(theta+m)
            cos_t = tf.matmul(y_pred, weights, name='cos_t')
            cos_t2 = tf.square(cos_t, name='cos_2')
            sin_t2 = tf.subtract(1., cos_t2, name='sin_2')
            sin_t = tf.sqrt(sin_t2, name='sin_t')
            cos_mt = s * tf.subtract(tf.multiply(cos_t, cos_m), tf.multiply(sin_t, sin_m), name='cos_mt')

            # this condition controls the theta+m should in range [0, pi]
            #      0<=theta+m<=pi
            #     -m<=theta<=pi-m
            cond_v = cos_t - threshold
            cond = tf.cast(tf.nn.relu(cond_v, name='if_else'), dtype=tf.bool)

            keep_val = s*(cos_t - mm)
            cos_mt_temp = tf.where(cond, cos_mt, keep_val)

            mask = tf.one_hot(y_true, depth=out_num, name='one_hot_mask')
            # mask = tf.squeeze(mask, 1)
            inv_mask = tf.subtract(1., mask, name='inverse_mask')

            s_cos_t = s * cos_t

            output = tf.add(tf.multiply(s_cos_t, inv_mask), tf.multiply(cos_mt_temp, mask), name='aam_loss_output')
        return output
    return additive_angular_margin_loss
        

def sigmoid_cross_entropy_loss(out_num:int):

    def CE_loss(y_true, y_pred):
        y_true = tf.cast(y_true, tf.int32)
        one_hot = tf.one_hot(y_true, depth=out_num, name='one_hot_mask')
        loss = keras.losses.categorical_crossentropy(one_hot, y_pred)
        return loss

    return CE_loss



def cross_entropy_loss(out_num:int):

    def CE_loss(y_true, y_pred):
        y_true = tf.cast(y_true, tf.int32)
        one_hot = tf.one_hot(y_true, depth=out_num, name='one_hot_mask')
        loss = keras.losses.categorical_crossentropy(one_hot, y_pred)
        return loss

    return CE_loss



def deep_speaker_loss(y_true, y_pred):
    """
    计算深度说话人嵌入的三元组损失
    
    Args:
        y_true: 标签（在这个实现中不使用）
        y_pred: 嵌入向量，结构为 [anchor, positive, negative] * batch_size
        
    Returns:
        平均三元组损失（不是总和）
    """
    # 固定批次大小以解决XLA编译问题
    # 使用静态操作而非动态分割，避免潜在的条件执行
    batch_size = tf.shape(y_pred)[0] // 3
    
    # 使用静态切片避免条件执行，解决XLA编译的梯度计算问题
    anchor = y_pred[:batch_size]
    positive_ex = y_pred[batch_size:2*batch_size]
    negative_ex = y_pred[2*batch_size:3*batch_size]
    
    # 计算余弦相似度
    sap = batch_cosine_similarity(anchor, positive_ex)  # anchor 和 positive 的相似度
    san = batch_cosine_similarity(anchor, negative_ex)  # anchor 和 negative 的相似度
    
    # 计算 triplet loss: max(0, similarity(anchor,negative) - similarity(anchor,positive) + alpha)
    loss = tf.maximum(san - sap + alpha, 0.0)
    
    # 返回所有triplet的平均损失（而不是总和），使梯度计算更稳定
    return tf.reduce_mean(loss)



if __name__ == "__main__":
    a = np.array([1,3,1,2])
    b = np.array([0.3,0.2,0.1,0.5])
    
    # loss = cross_entropy_loss(a, b)
    # print(loss)
