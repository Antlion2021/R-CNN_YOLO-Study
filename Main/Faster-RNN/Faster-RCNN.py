import torch
import torch.nn as nn
import torchvision
import math

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

def apply_regression_pred_to_anchor_or_proposals(
        box_transform_pred, anchor_or_proposals
):
    r"""
    :param box_transform_pred: (num_anchors_or_proposals, num_classes, 4)
    :param anchor_or_proposals:(num_anchors_or_proposals, 4)
    :return: pre_box: (num_anchors_or_proposals, num_classes, 4)
    """
    box_transform_pred = box_transform_pred.reshape(box_transform_pred.size(0), -1, 4)

    # Get cx, cy, w, h, from x1, y1, x2, y2
    w = anchor_or_proposals[:, 2] - anchor_or_proposals[:, 0]
    h = anchor_or_proposals[:, 3] - anchor_or_proposals[:, 1]
    center_x = anchor_or_proposals[:, 0] + 0.5 * w
    center_y = anchor_or_proposals[:, 1] + 0.5 * h

    # Get Transform param tx , ty, tw, th (dx,dy,dw,dh) in code
    dx = box_transform_pred[..., 0]
    dy = box_transform_pred[..., 1]
    dw = box_transform_pred[..., 2]
    dh = box_transform_pred[..., 3]
    # dh -> (num_anchor_or_proposals, num_classes)

    pred_center_x = w[:, None] * dx + center_x[:, None]
    pred_center_y = h[:, None] * dy + center_y[:, None]
    pred_w = torch.exp(dw) * w[:, None]
    pred_h = torch.exp(dh) * h[:, None]
    # pred_center_x ->(num_anchor_or_proposals, num_class)

    # Get pred_box x1, x2, y1, y2
    pred_box_x1 = pred_center_x - 0.5 * pred_w
    pred_box_y1 = pred_center_y - 0.5 * pred_h
    pred_box_x2 = pred_center_x + 0.5 * pred_w
    pred_box_y2 = pred_center_y + 0.5 * pred_h

    pred_box = torch.stack([pred_box_x1, pred_box_y1, pred_box_x2, pred_box_y2], dim=2)
    # pred_box -> (num_of_anchor_or_proposals, num_classes, 4)
    return pred_box

def sample_positive_negative(labels, positive_count, total_count):
    # Sample positive and negative proposals
    positive = torch.where(labels >= 1)[0]
    negative = torch.where(labels == 0)[0]
    num_pos = positive_count
    num_pos = min(positive.numel(), num_pos)
    num_neg = total_count - num_pos
    num_neg = min(negative.numel(), num_neg)
    perm_positive_idxs = torch.randperm(positive.numel(),
                                        device=positive.device)[:num_pos]
    perm_negative_idxs = torch.randperm(negative.numel(),
                                        device=negative.device)[:num_neg]
    pos_idxs = positive[perm_positive_idxs]
    neg_idxs = negative[perm_negative_idxs]
    sampled_pos_idx_mask = torch.zeros_like(labels, dtype=torch.bool)
    sampled_neg_idx_mask = torch.zeros_like(labels, dtype=torch.bool)
    sampled_pos_idx_mask[pos_idxs] = True
    sampled_neg_idx_mask[neg_idxs] = True
    return sampled_neg_idx_mask, sampled_pos_idx_mask

def clamp_box_to_image(box, image_shape):
    boxes_x1 = box[..., 0]
    boxes_y1 = box[..., 1]
    boxes_x2 = box[..., 2]
    boxes_y2 = box[..., 3]

    height, wight = image_shape[-2:]

    boxes_x1 = boxes_x1.clamp(min=0, max=wight)
    boxes_y1 = boxes_y1.clamp(min=0, max=height)
    boxes_x2 = boxes_x2.clamp(min=0, max=wight)
    boxes_y2 = boxes_y2.clamp(min=0, max=height)

    boxes = torch.cat([boxes_x1, boxes_y1, boxes_x2, boxes_y2], dim=-1)

    return boxes

def get_iou(boxes1, boxes2):
    r"""
    :param boxes1:(N x 4)
    :param boxes2:(M x 4)
    :return:IOU matrix of shape (N, M)
    """
    # Area of Boxes (x2 - x1) * (y2 - y1)
    area1 = (boxes1[:, 2] - boxes1[:, 0]) * (boxes1[:, 3] - boxes1[:, 1])
    area2 = (boxes2[:, 2] - boxes2[:, 0]) * (boxes2[:, 3] - boxes2[:, 1])

    # Get top left x1, y1
    x_left = torch.max(boxes1[:, None, 0], boxes2[:, 0]) # (N x M)
    y_top  = torch.max(boxes1[:, None, 1], boxes2[:, 1]) # (N x M)

    # Get bottom right x2, y2
    x_right = torch.min(boxes1[:, None, 2], boxes2[:, 2])
    y_bottom = torch.min(boxes1[:, None, 3], boxes2[:, 3])

    intersection = (x_right - x_left).clamp(min=0) * (y_bottom - y_top).clamp(min=0)
    union = area1[:, None] + area2 - intersection

    return  intersection / union # (N x M)

def box_to_transform_target(ground_truth_boxes, anchors_or_proposals):
    # Get Center_x,Center_y, W, H from anchors x1,y1,x2,y2
    widths = anchors_or_proposals[:, 2] - anchors_or_proposals[:, 0]
    heights = anchors_or_proposals[:, 3] - anchors_or_proposals[:, 1]
    center_x = anchors_or_proposals[:, 0] + 0.5 * widths
    center_y = anchors_or_proposals[:, 1] + 0.5 * heights

    # Get Center_x,Center_y, W, H from gt_boxes x1,y1,x2,y2
    gt_widths = ground_truth_boxes[:, 2] - ground_truth_boxes[:, 0]
    gt_heights = ground_truth_boxes[:, 3] - ground_truth_boxes[:, 1]
    gt_center_x = ground_truth_boxes[:, 0] + 0.5 * gt_widths
    gt_center_y = ground_truth_boxes[:, 1] + 0.5 * gt_heights

    target_dx = (gt_center_x - center_x) / widths
    target_dy = (gt_center_y - center_y) / heights
    target_dw = torch.log(gt_widths / widths)
    target_dh = torch.log(gt_heights / heights)

    regression_targets = torch.cat([target_dx, target_dy, target_dw, target_dh], dim=1)

    return regression_targets


class RPN(nn.Module):  # Pytorch_study RPN part: First Layer
    def __init__(self, in_channels=512):
        super(RPN, self).__init__()
        self.scales = [128, 256, 512]
        self.aspect_ratios = [0.5, 1.0, 2.0]
        # 3*3 conv
        self.num_anchors = len(self.scales) * len(self.aspect_ratios)

        self.rpn_conv = nn.Conv2d(in_channels,
                                  in_channels,
                                  kernel_size=3,
                                  stride=1,
                                  padding=1)
        # 1*1 Classification
        self.cls_layer = nn.Conv2d(in_channels,
                                   self.num_anchors,
                                   kernel_size=1,
                                   stride=1)
        # 1*1 Box regression
        self.bbox_reg_layer = nn.Conv2d(in_channels,
                                        self.num_anchors * 4,
                                        kernel_size=1,
                                        stride=1)

    def generate_anchors(self, image, feat):
        grid_h, grid_w = feat.shape[-2:]
        image_h, image_w = image.shape[-2:]

        stride_h = torch.tensor(image_h // grid_h,
                                dtype=torch.int64,
                                device=feat.device)
        stride_w = torch.tensor(image_w // grid_w,
                                dtype=torch.int64,
                                device=feat.device)
        scale = torch.totensor(self.scales,
                               dtype=feat.dtype,
                               device=feat.device)
        aspect_ratio = torch.tensor(self.aspect_ratios,
                                    dtype=feat.dtype,
                                    device=feat.device)
        # below code ensure h/w = aspect_ratio, h*w = 1
        h_ratio = torch.sqrt(aspect_ratio)
        w_ratio = 1 / h_ratio
        # Get Box H and W
        #     [3x1] * [1x3] -> [3x3].view(-1) -> len[9]
        ws = (w_ratio[:, None] * scale[None, :]).view(-1)
        hs = (h_ratio[:, None] * scale[None, :]).view(-1)

        base_anchor = torch.stack([-ws, -hs, ws, hs], dim=1) / 2
        base_anchor = base_anchor.round()
        # Get shift in x aix (0, 1,..., w_feat-1) * stride_w
        # image / feat = stride = 16
        shift_x = torch.arange(0, grid_w, device=feat.device,
                               dtype=torch.int32) * stride_w
        shift_y = torch.arange(0, grid_h, device=feat.device,
                               dtype=torch.int32) * stride_h
        # (H_feat, W_feat) grid
        shift_y, shift_x = torch.meshgrid(shift_y, shift_x, indexing='ij')
        shift_x = shift_x.reshape(-1)
        shift_y = shift_y.reshape(-1)
        shifts = torch.stack([shift_x, shift_y, shift_x, shift_y], dim=1)
        # shifts = (H_feat * W_heat, 4)

        # base_anchor = (num_anchor_per_location, 4)
        # shifts = (H_feat * W_heat, 4)

        anchors = shifts.view(-1, 1, 4) + base_anchor.view(1, -1, 4)
        # anchor -> (H_feat * W_feat,num_anchor_per_location,4)
        anchors = anchors.reshape(-1, 4)
        # anchors -> (H_feat * W_feat * num_anchor_per_location, 4)

        return anchors

    def filter_proposals(self, proposals, cls_score, image_shape):
        # Pre NMS Filters
        cls_score = cls_score.reshape(-1)
        cls_score = torch.sigmoid(cls_score)
        _, tpk_n_idx = cls_score.topk(10000)
        cls_score = cls_score[tpk_n_idx]
        proposals = proposals[tpk_n_idx]

        # Clamp box to image boundary
        proposals = clamp_box_to_image(proposals, image_shape)

        # NMS based on objects
        keep_mask = torch.zeros_like(cls_score, dtype=torch.bool)
        keep_indices = torch.ops.torchvision.nms(proposals,
                                                 cls_score,
                                                 0.7)
        post_nms_keep_indices = keep_indices[
            cls_score[keep_indices].sort(descending=True)[1]
        ]
        # Post NMS topk filtering
        proposals = proposals[post_nms_keep_indices[:2000]]
        cls_score = cls_score[post_nms_keep_indices[:2000]]
        return proposals, cls_score

    def assign_target_to_anchor(self, anchors, gt_boxes):
        # Get (gt_box, num_anchor) IOU matrix
        iou_matrix = get_iou(gt_boxes, anchors)
        best_match_iou, best_match_gt_index = iou_matrix.max(dim=0)

        # This copy will be needed later to add low
        # quality boxes
        best_match_gt_idx_pre_threshold = best_match_gt_index.clone()
        below_low_threshold = best_match_iou < 0.3
        between_threshold = (best_match_iou >= 0.3) & (best_match_iou < 0.7)
        best_match_gt_index[below_low_threshold] = -1
        best_match_gt_index[between_threshold] = -2

        # Low quality boxes
        best_anchor_iou_for_gt, _ = iou_matrix.max(dim=1)
        gt_pre_pair_with_highest_iou = torch.where(iou_matrix == best_anchor_iou_for_gt[:, None])

        # Get all the anchor index to update
        pre_inds_to_update = gt_pre_pair_with_highest_iou[1]
        best_match_gt_index[pre_inds_to_update] = best_match_gt_idx_pre_threshold[pre_inds_to_update]

        # Best match index is either valid or -1(background) or -2(ignore)
        match_gt_boxes = gt_boxes[best_match_gt_index.clamp(min=0)]

        # Set all foreground anchor label as 1
        labels = best_match_gt_index > 0
        labels = labels.to(torch.float32)

        # Set all background anchor label as 0
        background_anchor = best_match_gt_index == -1
        labels[background_anchor] = 0.0

        # Set all ignore anchor label as -1
        ignore_anchor = best_match_gt_index == -2
        labels[ignore_anchor] = -1.0

        # Later for classification we pick labels which have >=0
        return labels, match_gt_boxes



    def forward(self, image, feat, target):
        # Call RPN Layer
        rpn_feat = nn.ReLU()(self.rpn_conv(feat))
        cls_scores = self.cls_layer(rpn_feat)
        box_transform_pred = self.bbox_reg_layer(rpn_feat)

        # Generate Anchor
        anchors = self.generaate_anchors(image, feat)

        # ↓ Transform cls_scores and box_transform_per -> anchor.shape
        # cls_scores -> (Batch, number of Anchors per location, H_feat, W_feat)
        number_of_anchor_per_location = cls_scores.size(1)
        cls_scores = cls_scores.permute(0, 2, 3, 1)
        cls_scores = cls_scores.reshape(-1, 1)
        # cls_scores -> (Batch*H_feat*W_feat*number_of_per_location, 1)

        # box_transform_per -> (Batch, number_of_anchor_per_location*4, H_feat, W_feat)
        box_transform_pred = box_transform_pred.view(
            box_transform_pred.size(0),
            number_of_anchor_per_location,
            4,
            rpn_feat.size[-2],
            rpn_feat.size[-1]
        )
        box_transform_pred = box_transform_pred.permute(0, 3, 4, 1, 2)
        box_transform_pred = box_transform_pred.reshape(-1, 4)
        # box_transform_pred -> (B*H_feat*W_feat*number_of_anchor_per_location, 4)

        # Transform generate anchor to box_transform_pred
        proposals = apply_regression_pred_to_anchor_or_proposals(
            box_transform_pred.reshape(-1, 1, 4),
            anchors
        )
        proposals = proposals.reshape(proposals.size(0), 4)
        proposals, scores = self.filter_proposals(proposals, cls_scores.detach(),
                                                  image.shape)

        rpn_output = {
            'proposals': proposals,
            'cls_scores': cls_scores,
        }

        if not self.training or target is None:
            return rpn_output
        else:
            # in training
            # Assign gt box and label for each anchor
            labels_for_anchors, matched_gt_boxes_form_anchors = self.assign_target_to_anchor(
                anchors, target['bbox'][0]  # only one gt
            )
            # Based on gt assignment above, get regression target for anchors
            # matches_gt_box_form_anchor ->(Number of anchor in image, 4)
            # anchors -> (Number of anchors in image, 4)

            regression_targets = box_to_transform_target(matched_gt_boxes_form_anchors,anchors)
            sampled_neg_idx_mask, sampled_pos_idx_mask = sample_positive_negative(labels_for_anchors,
                                                                                  positive_count=128,
                                                                                  total_count=256)
            sampled_idxs = torch.where(sampled_pos_idx_mask | sampled_neg_idx_mask)[0]

            localization_loss = (
                    torch.nn.functional.smooth_l1_loss(
                        box_transform_pred[sampled_pos_idx_mask],
                        regression_targets[sampled_pos_idx_mask],
                        beta=1 / 9,
                        reduction="sum",
                    )
                    / (sampled_idxs.numel())
            )
            cls_loss = torch.nn.functional.binary_cross_entropy_with_logits(cls_scores[sampled_idxs].flatten(),
                                                                            labels_for_anchors[sampled_idxs].flatten())

            rpn_output['rpn_classification_loss'] = cls_loss
            rpn_output['rpn_localization_loss'] = localization_loss
            return rpn_output

class RIOHead(nn.Module):
    def __init__(self, num_classes, in_channels):
        super(RIOHead, self).__init__()
        self.num_classes = num_classes
        self.in_channels = in_channels
        self.pool_size = 7
        self.fc_inner_dim = 1024

        self.fc6 = nn.Linear(self.in_channels * self.pool_size * self.pool_size, self.fc_inner_dim)
        self.fc7 = nn.Linear(self.fc_inner_dim, self.fc_inner_dim)
        self.cls_layer = nn.Linear(self.fc_inner_dim, self.num_classes)
        self.bbox_reg_layer = nn.Linear(self.fc_inner_dim, self.num_classes * 4)

    def assign_target_to_proposals(self, proposals, gt_boxes, gt_labels):
        r"""
        Given a set of proposals and ground truth boxes and their respective labels.
        Use IOU to assign these proposals to some gt box or background
        :param proposals: (number_of_proposals, 4)
        :param gt_boxes: (number_of_gt_boxes, 4)
        :param gt_labels: (number_of_gt_boxes)
        :return:
            labels: (number_of_proposals)
            matched_gt_boxes: (number_of_proposals, 4)
        """
        # Get IOU Matrix between gt boxes and proposals
        iou_matrix = get_iou(gt_boxes, proposals)
        # For each gt box proposal find best matching gt box
        best_match_iou, best_match_gt_idx = iou_matrix.max(dim=0)
        background_proposals = (best_match_iou < self.iou_threshold) & (best_match_iou >= self.low_bg_iou)
        ignored_proposals = best_match_iou < self.low_bg_iou

        # Update best match of low IOU proposals to -1
        best_match_gt_idx[background_proposals] = -1
        best_match_gt_idx[ignored_proposals] = -2

        # Get best marching gt boxes for ALL proposals
        # Even background proposals would have a gt box assigned to it
        # Label will be used to ignore them later
        matched_gt_boxes_for_proposals = gt_boxes[best_match_gt_idx.clamp(min=0)]

        # Get class label for all proposals according to matching gt boxes
        labels = gt_labels[best_match_gt_idx.clamp(min=0)]
        labels = labels.to(dtype=torch.int64)

        # Update background proposals to be of label 0(background)
        labels[background_proposals] = 0

        # Set all to be ignored anchor labels as -1(will be ignored)
        labels[ignored_proposals] = -1

        return labels, matched_gt_boxes_for_proposals

    def forward(self, feat, proposals, image_shape, target):
        if self.training and target is not None:
            gt_boxes = target['bbox'][0]
            gt_labels = target['label'][0]
            # assign labels gt boxes for proposals
            labels, matched_gt_boxes_for_proposals = self.assign_target_to_proposals(proposals, gt_boxes, gt_labels)